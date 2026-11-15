"""Entrega dos produtos da API de produtos (presentes, diamantes, Snickers).

Regras importantes:
- Nenhuma mensagem para o cliente cita provedor, dono, URL ou detalhes da API.
  Os erros passam por ``public_error`` (mensagens formais e genéricas).
- Transições de status são atômicas para nunca comprar duas vezes o mesmo pedido.
- Falha com garantia de que nada foi processado: valor volta ao saldo do bot.
- Falha sem confirmação (timeout/502): pedido vai para análise e os donos são avisados.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
from decimal import Decimal
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home
from app.services.ffhub_shop import (
    FFHubResult,
    FFHubShopApi,
    admin_error,
    failure_is_safe_to_refund,
    failure_kind,
    public_error,
)
from app.utils import format_price

logger = logging.getLogger(__name__)

TOKEN_DELIVERIES = {"api_diamonds", "api_snickers"}
DIAMOND_POLL_SECONDS = 180

# Status de pedido usados por estes produtos.
#   sending    -> requisição em andamento (trava contra envio duplicado)
#   processing -> diamantes registrados, aguardando entrega
#   review     -> resultado não confirmado; equipe analisa
#   refunded   -> valor devolvido ao saldo do bot


def order_meta(order: dict) -> dict[str, Any]:
    try:
        data = json.loads(order.get("metadata") or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def valid_token(token: str) -> bool:
    token = (token or "").strip()
    return 6 <= len(token) <= 4000 and not any(ch.isspace() for ch in token)


def diamond_status_button(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Consultar andamento",
                    callback_data=f"xs:dst:{int(order_id)}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home")],
        ]
    )


def token_prompt(title: str, extra: str = "") -> str:
    return (
        "✅ <b>Pagamento confirmado!</b>\n\n"
        f"📦 Produto: <b>{title}</b>\n"
        f"{extra}"
        "\nAgora envie aqui o seu <b>token Rewards</b> (somente o token, "
        "em uma única mensagem).\n\n"
        "🔒 O token é usado apenas para concluir este pedido."
    )


class ApiOrders:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        api: FFHubShopApi,
    ) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.api = api

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _title(self, order: dict) -> str:
        product = self.config.products.get(order.get("product_code") or "")
        meta = order_meta(order)
        if order.get("product_code") == "gift" and meta.get("gift_name"):
            return f"🎁 {html.escape(str(meta['gift_name']))}"
        return product.title if product else str(order.get("product_code"))

    async def _send(self, user_id: int, text: str, markup=None) -> None:
        try:
            await self.bot.send_message(
                int(user_id), text, reply_markup=markup or back_home()
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao notificar cliente %s", user_id)

    async def alert_admins(self, order: dict, title: str, detail: str) -> None:
        text = (
            f"🚨 <b>{title}</b>\n"
            f"Pedido: <code>#{order['id']}</code>\n"
            f"Cliente: <code>{order['user_id']}</code>\n"
            f"Produto: {self._title(order)}\n"
            f"Valor: R$ {format_price(Decimal(str(order.get('amount') or 0)))}\n"
            f"Detalhe: <code>{html.escape(detail)[:500]}</code>\n\n"
            f"Resolver: <code>/reembolsar {order['id']}</code> ou "
            f"<code>/concluir {order['id']}</code>"
        )
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:  # noqa: BLE001
                pass

    async def refund_to_wallet(self, order: dict, reason: str) -> Decimal:
        amount = Decimal(str(order.get("amount") or 0)).quantize(Decimal("0.01"))
        cents = int((amount * 100).to_integral_value())
        new_balance = await self.db.credit_wallet(
            int(order["user_id"]),
            cents,
            order_id=int(order["id"]),
            description=f"Estorno do pedido #{order['id']}",
        )
        await self.db.update_order_status(int(order["id"]), "refunded", reason[:1000])
        return (Decimal(new_balance) / Decimal(100)).quantize(Decimal("0.01"))

    async def handle_failure(
        self,
        order: dict,
        result: FFHubResult,
        *,
        field: str,
    ) -> None:
        """Falha definitiva (não é caso de pedir outro token)."""
        reason = public_error(result, field=field)
        detail = admin_error(result)
        if failure_is_safe_to_refund(result):
            balance = await self.refund_to_wallet(order, detail)
            await self._send(
                order["user_id"],
                "⚠️ <b>Não foi possível concluir o seu pedido.</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 Pedido: <code>#{order['id']}</code>\n"
                f"ℹ️ {reason}\n\n"
                f"💰 O valor de <b>R$ {format_price(Decimal(str(order['amount'])))}</b> "
                "foi devolvido ao seu saldo no bot.\n"
                f"👛 Saldo atual: <b>R$ {format_price(balance)}</b>",
            )
            if failure_kind(result) == "config":
                await self.alert_admins(
                    order, "Produto API falhou (estornado ao saldo)", detail
                )
            return

        await self.db.update_order_status(int(order["id"]), "review", detail)
        await self._send(
            order["user_id"],
            "⏳ <b>Pedido em análise</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 Pedido: <code>#{order['id']}</code>\n"
            f"ℹ️ {reason}\n\n"
            "Nossa equipe vai verificar e retornar o mais rápido possível.",
        )
        await self.alert_admins(order, "Pedido precisa de análise", detail)

    # ------------------------------------------------------------------
    # Presentes (UID informado antes do pagamento)
    # ------------------------------------------------------------------
    async def deliver_gift(self, order: dict) -> bool:
        order_id = int(order["id"])
        if order["status"] not in {"pending", "paid", "expired"}:
            # Já em envio, entregue, estornado ou em análise.
            return order["status"] in {"sending", "delivered", "review", "refunded"}
        if not await self.db.transition_order_status(order_id, order["status"], "sending"):
            return True

        meta = order_meta(order)
        gift_id = str(meta.get("gift_id") or "")
        uid = str(order.get("game_id") or "")
        if not gift_id or not uid:
            fake = FFHubResult(ok=False, status=404, data={}, error="dados do pedido ausentes")
            await self.handle_failure(order, fake, field="UID")
            return False

        result = await self.api.buy_gift(gift_id, uid)
        if not result.ok:
            logger.warning(
                "Presente falhou pedido=%s %s", order_id, admin_error(result)
            )
            await self.handle_failure(order, result, field="UID")
            return False

        data = result.data if isinstance(result.data, dict) else {}
        confirmed = data.get("delivery_confirmed")
        await self.db.update_order_status(order_id, "delivered", json.dumps(data)[:2000])
        await self.db.decrement_stock("gift")
        status_line = (
            "✅ Entrega confirmada."
            if confirmed in (None, True, "true", 1)
            else "⏳ Envio realizado; a confirmação pode levar alguns minutos."
        )
        await self._send(
            order["user_id"],
            "🎁 <b>PRESENTE ENVIADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Item: <b>{html.escape(str(meta.get('gift_name') or 'Presente'))}</b>\n"
            f"🆔 UID: <code>{html.escape(uid)}</code>\n"
            f"📄 Pedido: <code>#{order_id}</code>\n\n"
            f"{status_line}",
        )
        return True

    # ------------------------------------------------------------------
    # Diamantes e Snickers (token Rewards informado após o pagamento)
    # ------------------------------------------------------------------
    async def ask_token(self, order: dict) -> bool:
        if order["status"] in {"awaiting_id", "sending", "processing", "delivered", "review", "refunded"}:
            return True
        await self.db.update_order_status(int(order["id"]), "awaiting_id")
        product = self.config.products.get(order["product_code"])
        extra = ""
        if product and product.delivery == "api_diamonds":
            extra = "💎 A entrega dos diamantes é feita pela equipe após o registro.\n"
        await self._send(
            order["user_id"],
            token_prompt(product.title if product else "Produto", extra),
        )
        return True

    async def submit_token(self, order: dict, token: str) -> str:
        """Processa o token enviado pelo cliente. Retorna o texto da resposta."""
        order_id = int(order["id"])
        product = self.config.products.get(order["product_code"])
        if not product or product.delivery not in TOKEN_DELIVERIES:
            return "⚠️ Não consegui localizar o produto deste pedido. Fale com o suporte."

        token = (token or "").strip()
        if not valid_token(token):
            return (
                "⚠️ <b>Token inválido.</b>\n"
                "Envie somente o token Rewards, sem espaços, em uma única mensagem."
            )

        if not await self.db.transition_order_status(order_id, "awaiting_id", "sending"):
            return "⏳ Este pedido já está sendo processado."

        if product.delivery == "api_diamonds":
            qty = str(product.code.split("_", 1)[1])
            result = await self.api.buy_diamonds(token, qty)
        else:
            result = await self.api.redeem_snickers(token)

        if not result.ok:
            logger.warning(
                "Produto por token falhou pedido=%s %s", order_id, admin_error(result)
            )
            if failure_kind(result) == "invalid":
                await self.db.update_order_status(order_id, "awaiting_id", admin_error(result))
                return (
                    "⚠️ <b>Não foi possível usar este token.</b>\n\n"
                    f"ℹ️ {public_error(result, field='token')}\n\n"
                    "Envie um novo token Rewards para concluir o pedido "
                    f"<code>#{order_id}</code>."
                )
            fresh = await self.db.get_order(order_id) or order
            await self.handle_failure(fresh, result, field="token")
            return ""

        data = result.data if isinstance(result.data, dict) else {}
        if product.delivery == "api_snickers":
            await self.db.update_order_status(order_id, "delivered", json.dumps(data)[:2000])
            await self.db.decrement_stock(product.code)
            return (
                "🍫 <b>SNICKERS RESGATADO</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 Pedido: <code>#{order_id}</code>\n\n"
                "✅ Resgate concluído com sucesso."
            )

        remote_id = str(data.get("order_id") or "").strip()
        meta = order_meta(order)
        meta["remote_order_id"] = remote_id
        await self.db.set_order_metadata(order_id, json.dumps(meta))
        await self.db.update_order_status(order_id, "processing", json.dumps(data)[:2000])
        await self.db.decrement_stock(product.code)
        if not remote_id:
            fresh = await self.db.get_order(order_id) or order
            await self.alert_admins(
                fresh, "Diamantes registrados sem número de acompanhamento", admin_error(result)
            )
        return (
            "💎 <b>PEDIDO DE DIAMANTES REGISTRADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Pacote: <b>{product.title}</b>\n"
            f"📄 Pedido: <code>#{order_id}</code>\n"
            "📌 Status: <b>Aguardando entrega</b>\n\n"
            "Você será avisado aqui assim que os diamantes forem entregues."
        )

    async def check_diamond(self, order: dict, *, notify: bool = True) -> str:
        """Atualiza um pedido de diamantes. Retorna o status local."""
        if order["status"] != "processing":
            return str(order["status"])
        remote_id = str(order_meta(order).get("remote_order_id") or "")
        if not remote_id:
            return "processing"
        result = await self.api.diamond_order(remote_id)
        if not result.ok:
            return "processing"
        pedido = result.data.get("pedido") if isinstance(result.data, dict) else None
        if not isinstance(pedido, dict):
            return "processing"
        remote_status = str(pedido.get("status") or "").upper()
        order_id = int(order["id"])

        if remote_status == "ENTREGUE":
            if await self.db.transition_order_status(
                order_id, "processing", "delivered", json.dumps(pedido)[:2000]
            ):
                if notify:
                    nick = pedido.get("nick")
                    nick_line = (
                        f"👤 Jogador: <b>{html.escape(str(nick))}</b>\n" if nick else ""
                    )
                    await self._send(
                        order["user_id"],
                        "💎 <b>DIAMANTES ENTREGUES!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 Pacote: <b>{self._title(order)}</b>\n"
                        f"{nick_line}"
                        f"📄 Pedido: <code>#{order_id}</code>\n\n"
                        "✅ Confira sua conta no jogo.",
                    )
            return "delivered"

        if remote_status == "REEMBOLSADO":
            if await self.db.transition_order_status(order_id, "processing", "sending"):
                balance = await self.refund_to_wallet(order, json.dumps(pedido)[:1000])
                if notify:
                    await self._send(
                        order["user_id"],
                        "⚠️ <b>Pedido de diamantes não concluído</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📄 Pedido: <code>#{order_id}</code>\n"
                        "Não foi possível concluir a entrega deste pedido.\n\n"
                        f"💰 O valor de <b>R$ {format_price(Decimal(str(order['amount'])))}</b> "
                        "foi devolvido ao seu saldo no bot.\n"
                        f"👛 Saldo atual: <b>R$ {format_price(balance)}</b>",
                    )
            return "refunded"

        return "processing"

    async def watch_diamonds(self) -> None:
        """Acompanha pedidos de diamantes até a entrega ou estorno."""
        await asyncio.sleep(30)
        while True:
            try:
                if self.api.configured:
                    for order in await self.db.orders_with_status("processing", limit=100):
                        product = self.config.products.get(order["product_code"])
                        if not product or product.delivery != "api_diamonds":
                            continue
                        try:
                            await self.check_diamond(order)
                        except Exception:  # noqa: BLE001
                            logger.exception("Falha ao consultar diamantes pedido=%s", order["id"])
                        await asyncio.sleep(1)
            except Exception:  # noqa: BLE001
                logger.exception("Erro no acompanhamento de diamantes")
            await asyncio.sleep(DIAMOND_POLL_SECONDS)
