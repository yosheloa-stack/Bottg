"""Entrega automática de produtos após confirmação de pagamento.

Este módulo é chamado tanto pelo webhook do Mercado Pago quanto pela
verificação manual ("Já paguei"). É idempotente: um pedido já entregue
não é entregue novamente.
"""
from __future__ import annotations

import logging

from aiogram import Bot

from app import texts
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home
from app.services.autolike import AutoLikeApi
from app.services.passe import PasseApi

logger = logging.getLogger(__name__)


class DeliveryService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        autolike: AutoLikeApi,
        passe: PasseApi,
    ) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.autolike = autolike
        self.passe = passe

    async def fulfill_order(self, order_id: int) -> bool:
        """Entrega o pedido. Retorna True se entregue com sucesso.

        Idempotente: se já entregue, retorna True sem reenviar.
        """
        order = await self.db.get_order(order_id)
        if not order:
            logger.warning("fulfill_order: pedido %s não encontrado", order_id)
            return False

        if order["status"] == "delivered":
            return True

        product = self.config.products.get(order["product_code"])
        if not product:
            logger.error("Produto desconhecido no pedido %s", order_id)
            await self._notify_failure(order)
            return False

        # Marca como pago antes de tentar entregar
        await self.db.update_order_status(order_id, "paid")

        game_id = order["game_id"]
        try:
            if product.delivery == "passe":
                result = await self.passe.send_passe(game_id)
            elif product.delivery == "autolike":
                result = await self.autolike.add_auto(game_id, days=product.days)
            else:
                logger.error("Tipo de entrega desconhecido: %s", product.delivery)
                await self._notify_failure(order)
                return False
        except Exception:  # noqa: BLE001
            logger.exception("Erro na entrega do pedido %s", order_id)
            await self._notify_failure(order)
            return False

        if not result.ok:
            logger.error(
                "Entrega falhou pedido %s: status=%s data=%s err=%s",
                order_id,
                result.status,
                result.data,
                result.error,
            )
            await self.db.update_order_status(order_id, "failed", str(result.data))
            await self._notify_failure(order)
            return False

        await self.db.update_order_status(order_id, "delivered", str(result.data))
        # Dá baixa no estoque (ignorado quando ilimitado/-1)
        await self.db.decrement_stock(order["product_code"])
        await self._notify_success(order, product, result.data)
        return True

    async def _notify_success(self, order: dict, product, data: dict) -> None:
        detail = ""
        msg = data.get("mensagem") or data.get("message") if isinstance(data, dict) else ""
        if msg:
            detail = f"ℹ️ {msg}"
        try:
            if product.delivery == "passe":
                text = texts.DELIVERY_SUCCESS_PASSE.format(
                    game_id=order["game_id"], detail=detail
                )
            else:
                text = texts.DELIVERY_SUCCESS_AUTOLIKE.format(
                    game_id=order["game_id"], days=product.days, detail=detail
                )
            await self.bot.send_message(
                order["user_id"], text, reply_markup=back_home()
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao notificar sucesso ao usuário %s", order["user_id"])

    async def _notify_failure(self, order: dict) -> None:
        try:
            await self.bot.send_message(
                order["user_id"],
                texts.DELIVERY_FAILED.format(
                    game_id=order["game_id"], order_id=order["id"]
                ),
                reply_markup=back_home(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao notificar erro ao usuário %s", order["user_id"])

        # Notifica admins
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(
                    admin_id,
                    f"🚨 <b>Entrega falhou</b>\n"
                    f"Pedido #{order['id']}\n"
                    f"Usuário: <code>{order['user_id']}</code>\n"
                    f"Produto: {order['product_code']}\n"
                    f"ID jogo: <code>{order['game_id']}</code>",
                )
            except Exception:  # noqa: BLE001
                pass
