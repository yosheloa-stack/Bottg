"""Ativação do Auto-Like Premium após confirmação do PIX."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import texts
from app.autolike_report import REGIONS, activation_text, send_fancy
from app.outgoing import keep_raw_output, reset_raw_output
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

    if product.delivery in {"autosystem_autolike", "ffhub_autolike"}:
        # Antes de ativar, o cliente escolhe a região (servidor) da conta.
        token = keep_raw_output()  # mantém as bandeiras nos botões
        try:
            await message.reply(
                "🌍 <b>Escolha a região da sua conta</b>\n"
                f"🆔 UID: <code>{uid}</code>\n\n"
                "Toque na sigla do servidor do seu Free Fire "
                "(ex.: BR = Brasil, IND = Índia).",
                reply_markup=_region_keyboard(int(order["id"])),
            )
        finally:
            reset_raw_output(token)
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
            error = (
                result.error
                or (result.data.get("erro") if isinstance(result.data, dict) else None)
                or (result.data.get("error") if isinstance(result.data, dict) else None)
                or "Não foi possível concluir o envio."
            )
            await sending.edit_text(
                "⚠️ <b>Pagamento confirmado, mas o envio falhou.</b>\n\n"
                f"🆔 UID: <code>{uid}</code>\n"
                f"Erro: {error}\n\n"
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


async def _activate_autolike(
    message: Message,
    db: Database,
    config: Config,
    autosystem_likes_api: AutoSystemLikesApi,
    order: dict,
    uid: str,
    region: str,
) -> None:
    """Cria a assinatura (220 ou 500/1000) depois que a região foi escolhida."""
    user_id = int(order["user_id"])
    product = config.products.get(order["product_code"])
    if not product:
        await db.update_order_status(order["id"], "failed")
        await message.answer(
            "⚠️ Não consegui localizar o produto deste pedido. "
            "Fale com o suporte.",
            reply_markup=back_home(),
        )
        return
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
                "⚠️ O serviço não está configurado no momento. "
                "Fale com o suporte."
            )
            return

        await db.create_autosystem_autolike_subscription(
            order_id=order["id"],
            user_id=user_id,
            game_id=uid,
            days_total=days,
        )
        await db.set_autolike_region(order_id=order["id"], region=region)
        await db.update_order_status(order["id"], "active")
        await db.decrement_stock(order["product_code"])

        await send_fancy(
            message.bot,
            db,
            message.chat.id,
            activation_text(
                plan="220 LIKE",
                uid=uid,
                region=region,
                days=days,
                per_send="220 likes",
            ),
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
        user_id=user_id,
        game_id=uid,
        days_total=days,
    )
    await db.set_autolike_region(order_id=order["id"], region=region)
    await db.update_order_status(order["id"], "active")

    await send_fancy(
        message.bot,
        db,
        message.chat.id,
        activation_text(
            plan="500/1000+ LIKE",
            uid=uid,
            region=region,
            days=days,
            per_send="500 a 1.000 likes",
        ),
        reply_markup=back_home(),
    )


def _region_keyboard(order_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{flag} {code}",
            callback_data=f"alreg:{order_id}:{code}",
        )
        for code, flag in REGIONS.items()
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("alreg:"))
async def choose_autolike_region(
    callback: CallbackQuery,
    db: Database,
    config: Config,
    autosystem_likes_api: AutoSystemLikesApi,
) -> None:
    try:
        _, raw_order, region = (callback.data or "").split(":", 2)
        order_id = int(raw_order)
    except ValueError:
        await callback.answer()
        return
    region = region.upper()
    order = await db.get_order(order_id)
    if (
        region not in REGIONS
        or not order
        or int(order["user_id"]) != callback.from_user.id
        or order.get("status") != "awaiting_id"
        or not order.get("game_id")
    ):
        await callback.answer("Esse cadastro já foi concluído.")
        return

    await callback.answer(f"Região {region} selecionada")
    if not callback.message:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await _activate_autolike(
        callback.message,
        db,
        config,
        autosystem_likes_api,
        order,
        str(order["game_id"]),
        region,
    )
