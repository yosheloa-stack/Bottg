"""Entrega automática de produtos após confirmação de pagamento.

Este módulo é chamado tanto pelo webhook do Mercado Pago quanto pela
verificação manual ("Já paguei"). É idempotente: um pedido já entregue
não é entregue novamente.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot

from app import texts
from app.announcements import announce_purchase
from app.config import Config
from app.database import Database
from app.keyboards.inline import add_group_button, back_home
from app.services.autolike import AutoLikeApi
from app.services.passe import PasseApi
from app.utils import format_price

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

    async def _remove_payment_message(self, order: dict) -> None:
        chat_id = order.get("payment_chat_id")
        message_id = order.get("payment_message_id")
        if chat_id is None or message_id is None:
            return

        try:
            await self.bot.delete_message(
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
            logger.info(
                "Mensagem PIX removida após aprovação | pedido=%s",
                order.get("id"),
            )
            await self.db.clear_order_payment_message(int(order["id"]))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Não foi possível remover mensagem PIX | pedido=%s chat=%s msg=%s",
                order.get("id"),
                chat_id,
                message_id,
            )

    async def fulfill_order(self, order_id: int) -> bool:
        """Entrega o pedido. Retorna True se entregue com sucesso.

        Idempotente: se já entregue, retorna True sem reenviar.
        """
        order = await self.db.get_order(order_id)
        if not order:
            logger.warning("fulfill_order: pedido %s não encontrado", order_id)
            return False

        # fulfill_order só é chamado após pagamento aprovado (ou saldo interno).
        # Portanto, se houver QR Code salvo, remove a mensagem antes da entrega.
        await self._remove_payment_message(order)

        if order["status"] == "delivered":
            return True

        product = self.config.products.get(order["product_code"])
        if not product:
            logger.error("Produto desconhecido no pedido %s", order_id)
            await self._notify_failure(order)
            return False

        try:
            await announce_purchase(
                self.bot,
                self.db,
                order,
                product.title,
                product.delivery,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao anunciar compra do pedido %s", order_id)

        # Auto Likes pagos: o pagamento libera a etapa de informar o UID.
        if product.delivery in {
            "ffhub_autolike",
            "ffhub_like_once",
            "autosystem_autolike",
        }:
            if order["status"] in {"awaiting_id", "active"}:
                return True
            await self.db.update_order_status(order_id, "awaiting_id")
            days = int(order.get("plan_days") or product.days or 0)
            if product.delivery == "ffhub_like_once":
                prompt = texts.LIKE2_SINGLE_ASK_ID
            elif product.delivery == "autosystem_autolike":
                prompt = (
                    "✅ <b>Pagamento confirmado!</b>\n\n"
                    "❤️ Seu <b>Auto Like 220</b> está liberado.\n"
                    f"📆 Período: <b>{days} dias</b>\n\n"
                    "Agora envie somente o <b>UID</b> da conta que vai receber "
                    "os likes."
                )
            else:
                prompt = (
                    "✅ <b>Pagamento confirmado!</b>\n\n"
                    "💎 Seu <b>Auto Like 500</b> está liberado.\n"
                    f"📆 Período: <b>{days} dias</b>\n\n"
                    "Agora envie somente o <b>UID</b> da conta que vai receber "
                    "os likes."
                )
            try:
                await self.bot.send_message(
                    order["user_id"],
                    prompt,
                    reply_markup=back_home(),
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Falha ao solicitar UID do pedido %s", order_id
                )
            return True

        if product.delivery == "balance_topup":
            try:
                amount = Decimal(str(order["amount"])).quantize(Decimal("0.01"))
                amount_cents = int((amount * 100).to_integral_value())
                new_balance = await self.db.credit_wallet(
                    int(order["user_id"]),
                    amount_cents,
                    order_id=order_id,
                    description="Recarga via PIX",
                )
                await self.db.update_order_status(order_id, "delivered")
                await self.bot.send_message(
                    int(order["user_id"]),
                    "✅ <b>SALDO ADICIONADO</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Crédito: <b>R$ {format_price(amount)}</b>\n"
                    f"👛 Saldo atual: <b>R$ {format_price(Decimal(new_balance) / Decimal(100))}</b>",
                    reply_markup=back_home(),
                )
                return True
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao creditar saldo pedido %s", order_id)
                await self._notify_failure(order)
                return False

        if product.delivery == "reseller_access":
            try:
                await self.db.activate_reseller(
                    int(order["user_id"]),
                    order_id=order_id,
                )
                await self.db.update_order_status(order_id, "delivered")
                await self.bot.send_message(
                    int(order["user_id"]),
                    "🤝 <b>REVENDA ATIVADA!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Sua conta agora recebe automaticamente os preços de "
                    "revendedor nos produtos elegíveis.\n\n"
                    "Abra o menu e toque em <b>Revenda</b> para acessar sua área.",
                    reply_markup=back_home(),
                )
                return True
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao ativar revendedor pedido %s", order_id)
                await self._notify_failure(order)
                return False

        if product.delivery == "bot_rental":
            try:
                days = int(order.get("plan_days") or product.days or 30)
                rental_id = await self.db.create_bot_rental(
                    order_id,
                    int(order["user_id"]),
                    days=days,
                )
                await self.db.update_order_status(order_id, "delivered")
                me = await self.bot.get_me()
                add_url = (
                    f"https://t.me/{me.username}?startgroup=rent_{rental_id}"
                )
                await self.bot.send_message(
                    int(order["user_id"]),
                    "🤖 <b>ALUGUEL CONFIRMADO!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📆 Período: <b>{days} dias</b>\n"
                    "👥 Licença: <b>1 grupo</b>\n"
                    f"📄 Código da licença: <code>#{rental_id}</code>\n\n"
                    "Agora toque no botão abaixo, escolha o seu grupo e adicione "
                    "o Aurora System. A licença será vinculada automaticamente.\n\n"
                    "Se o Telegram não enviar a ativação sozinho, use no grupo:\n"
                    f"<code>/ativarbot {rental_id}</code>",
                    reply_markup=add_group_button(add_url),
                )
                return True
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao criar aluguel pedido %s", order_id)
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
