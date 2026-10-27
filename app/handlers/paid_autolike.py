"""Ativação do Auto-Like Premium após confirmação do PIX."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app import texts
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
from app.utils import clean_game_id

router = Router(name="paid_autolike")


def _valid_uid(uid: str) -> bool:
    return uid.isdigit() and 8 <= len(uid) <= 11


@router.message(F.text)
async def receive_paid_autolike_uid(
    message: Message,
    db: Database,
    config: Config,
    ffhub_shop: FFHubShopApi,
    autosystem_likes_api: AutoSystemLikesApi,
) -> None:
    if not message.from_user or message.chat.type != "private":
        return

    order = await db.get_awaiting_id_order(message.from_user.id)
    if not order:
        return

    uid = clean_game_id(message.text or "")
    if not _valid_uid(uid):
        await message.reply(
            "⚠️ <b>UID inválido.</b>\n"
            "Envie somente o ID numérico da conta, com 8 a 11 dígitos."
        )
        return

    product = config.products.get(order["product_code"])
    if not product or product.delivery not in {
        "ffhub_autolike",
        "ffhub_like_once",
        "autosystem_autolike",
    }:
        await db.update_order_status(order["id"], "failed")
        await message.reply(
            "⚠️ Não consegui localizar o produto deste pedido. "
            "Fale com o suporte.",
            reply_markup=back_home(),
        )
        return

    await db.set_order_game_id(order["id"], uid)

    days = int(order.get("plan_days") or product.days or 0)

    if product.delivery == "autosystem_autolike":
        if not 1 <= days <= 90:
            await db.update_order_status(order["id"], "failed")
            await message.reply(
                "⚠️ O período deste pedido está inválido. Fale com o suporte."
            )
            return

        if not autosystem_likes_api.enabled:
            await db.update_order_status(order["id"], "failed")
            await message.reply(
                "⚠️ O serviço de envio está indisponível no momento. "
                "Fale com o suporte."
            )
            return

        await db.create_autosystem_autolike_subscription(
            order_id=order["id"],
            user_id=message.from_user.id,
            game_id=uid,
            days_total=days,
        )
        await db.update_order_status(order["id"], "active")
        await db.decrement_stock(order["product_code"])

        await message.reply(
            "❤️ <b>AUTO LIKE 220 ATIVADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"📆 Período: <b>{days} dias</b>\n"
            "❤️ Por envio: <b>220 likes</b>\n"
            "⏰ Horário fixo: <b>09:00 da manhã</b>\n\n"
            "✅ O primeiro envio acontecerá na próxima janela das 09:00.",
            reply_markup=back_home(),
        )
        return

    if product.delivery == "ffhub_like_once":
        await db.update_order_status(order["id"], "paid")
        sending = await message.reply("💎 Enviando seus likes...")
        result = await ffhub_shop.send_paid_likes(uid)

        if not result.ok:
            await db.update_order_status(
                order["id"],
                "failed",
                str(result.data or result.error or ""),
            )
            await sending.edit_text(
                "⚠️ <b>Pagamento confirmado, mas o envio falhou.</b>\n\n"
                f"🆔 UID: <code>{uid}</code>\n\n"
                "O pedido ficou registrado para suporte."
            )
            return

        data = result.data if isinstance(result.data, dict) else {}
        parsed_like = parse_like_delivery(data)
        sent = parsed_like["sent"]
        sent_display = sent if sent is not None else "—"
        nick = parsed_like["nickname"]

        await db.update_order_status(order["id"], "delivered", str(data))
        await db.decrement_stock(order["product_code"])

        await sending.edit_text(
            "💚 <b>LIKE2 ENVIADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Jogador: <b>{nick}</b>\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"❤️ Enviados: <b>+{sent_display}</b>\n\n"
            "✅ Pedido concluído com sucesso.",
            reply_markup=back_home(),
        )
        return

    if not 1 <= days <= 365:
        await db.update_order_status(order["id"], "failed")
        await message.reply(
            "⚠️ O período deste pedido está inválido. Fale com o suporte."
        )
        return

    await db.create_ffhub_autolike_subscription(
        order_id=order["id"],
        user_id=message.from_user.id,
        game_id=uid,
        days_total=days,
    )
    await db.update_order_status(order["id"], "active")

    await message.reply(
        "💎 <b>AUTO LIKE 500 ATIVADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 UID: <code>{uid}</code>\n"
        f"📆 Período: <b>{days} dias</b>\n"
        "❤️ Por envio: <b>aprox. 500 a 1.000 likes</b>\n"
        "🔁 Frequência: <b>1 envio por dia</b>\n"
        "⏰ Horário fixo: <b>09:00 da manhã</b>\n\n"
        "✅ O primeiro envio acontecerá na próxima janela das 09:00.",
        reply_markup=back_home(),
    )
