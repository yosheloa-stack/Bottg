"""Comando de Auto Like FFHub exclusivo dos donos."""
from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import Config
from app.database import Database
from app.autolike_report import REGIONS, activation_text, send_fancy
from app.owner_auth import resolve_owner_id
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
from app.utils import clean_game_id

router = Router(name="owner_autolike")


TZ_BR = ZoneInfo("America/Sao_Paulo")


def _valid_uid(uid: str) -> bool:
    return uid.isdigit() and 8 <= len(uid) <= 11


def _to_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip().replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fmt_time(timestamp: int | None) -> str:
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(int(timestamp), TZ_BR).strftime("%d/%m/%Y às %H:%M")


@router.message(Command("autolike"))
async def cmd_owner_autolike(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    ffhub_shop: FFHubShopApi,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        await message.reply("🔒 <b>Comando exclusivo dos donos.</b>")
        return

    args = (command.args or "").split()
    if not args:
        await message.reply(
            "🔥 <b>AUTO LIKE 500/1000 • DONO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Ativar:\n"
            "<code>/autolike UID DIAS REGIÃO</code>\n\n"
            "Exemplo:\n"
            "<code>/autolike 813856263 7 BR</code>\n"
            "Região (opcional, padrão BR): BR, IND, US, NA, SAC, ME, ID, TH, VN, SG, PK, BD, TW, RU, EU\n\n"
            "Gerenciar:\n"
            "<code>/autolike list</code>\n"
            "<code>/autolike status UID</code>\n"
            "<code>/autolike cancel UID</code>\n"
            "<code>/removerid</code> — remover ID do 500 e 220\n\n"
            "✅ 1 envio por dia pela FFHub\n"
            "✅ aproximadamente 500 a 1.000 likes por envio\n"
            "✅ horário fixo: 05:00 da manhã"
        )
        return

    action = args[0].lower()

    if action in {"list", "lista"}:
        subs = await db.active_ffhub_autolikes_with_stats(limit=50)
        if not subs:
            await message.reply("🔥 <b>Auto Likes ativos</b>\n\nNenhum ativo.")
            return

        lines = ["🔥 <b>AUTO LIKES ATIVOS</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for sub in subs:
            done = int(sub.get("sends_done") or 0)
            total = int(sub.get("days_total") or 0)
            total_sent = int(sub.get("total_likes_sent") or 0)
            last_sent = sub.get("last_likes_sent")
            nick = html.escape(str(sub.get("nickname") or "Jogador"))
            lines.extend(
                [
                    f"👤 <b>{nick}</b>",
                    f"🆔 <code>{sub['game_id']}</code>",
                    f"📆 Envios: <b>{done}/{total}</b>",
                    f"❤️ Total enviado: <b>+{total_sent}</b>",
                    f"📈 Último envio: <b>+{int(last_sent or 0)}</b>",
                    f"⏰ Próximo: <b>{_fmt_time(sub.get('next_send_at'))}</b>",
                    "",
                ]
            )
        await message.reply("\n".join(lines).rstrip())
        return

    if action in {"status", "ver"}:
        if len(args) < 2:
            await message.reply("Use: <code>/autolike status UID</code>")
            return

        uid = clean_game_id(args[1])
        if not _valid_uid(uid):
            await message.reply("⚠️ UID inválido.")
            return

        sub = await db.get_ffhub_autolike_status(uid)
        if not sub:
            await message.reply("ℹ️ Nenhum Auto Like encontrado para esse UID.")
            return

        done = int(sub.get("sends_done") or 0)
        total = int(sub.get("days_total") or 0)
        remaining = max(0, total - done)
        total_sent = int(sub.get("total_likes_sent") or 0)
        last_sent = int(sub.get("last_likes_sent") or 0)
        nick = html.escape(str(sub.get("nickname") or "Jogador"))
        status_label = {
            "active": "🟢 Ativo",
            "completed": "✅ Concluído",
            "cancelled": "⛔ Cancelado",
        }.get(str(sub.get("status")), str(sub.get("status") or "—"))

        lines = [
            "🔥 <b>STATUS DO AUTO LIKE</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 Nick: <b>{nick}</b>",
            f"🆔 UID: <code>{uid}</code>",
            f"📌 Status: <b>{status_label}</b>",
            f"📆 Envios: <b>{done}/{total}</b>",
            f"⌛ Restantes: <b>{remaining}</b>",
            f"❤️ Total enviado: <b>+{total_sent}</b>",
            f"📈 Último envio: <b>+{last_sent}</b>",
        ]
        if sub.get("last_likes_before") is not None:
            lines.append(f"📊 Likes antes: <b>{sub['last_likes_before']}</b>")
        if sub.get("last_likes_after") is not None:
            lines.append(f"📊 Likes depois: <b>{sub['last_likes_after']}</b>")
        if sub.get("status") == "active":
            lines.append(
                f"⏰ Próximo envio: <b>{_fmt_time(sub.get('next_send_at'))}</b>"
            )
        lines.append(f"🕒 Cadastrado: <b>{_fmt_time(sub.get('created_at'))}</b>")

        await message.reply("\n".join(lines))
        return

    if action in {"cancel", "cancelar", "del", "remove"}:
        if len(args) < 2:
            await message.reply(
                "Use: <code>/autolike cancel UID</code>"
            )
            return
        uid = clean_game_id(args[1])
        if not _valid_uid(uid):
            await message.reply("⚠️ UID inválido.")
            return

        removed = await db.cancel_autolike_by_uid(uid)
        await message.reply(_removed_text(uid, removed))
        return

    uid = clean_game_id(args[0])
    if not _valid_uid(uid):
        await message.reply(
            "⚠️ UID inválido.\n"
            "Use: <code>/autolike UID DIAS</code>"
        )
        return

    if len(args) < 2:
        await message.reply(
            "⚠️ Informe a quantidade de dias.\n"
            "Exemplo: <code>/autolike 813856263 7</code>"
        )
        return

    try:
        days = int(args[1])
    except ValueError:
        await message.reply("⚠️ A quantidade de dias precisa ser um número.")
        return

    if not 1 <= days <= 365:
        await message.reply("⚠️ Use entre <b>1 e 365 dias</b>.")
        return

    region = args[2].upper() if len(args) >= 3 else "BR"
    if region not in REGIONS:
        await message.reply(
            "⚠️ Região inválida. Use uma destas siglas:\n"
            + ", ".join(REGIONS)
        )
        return

    if not ffhub_shop.configured:
        await message.reply(
            "⚠️ <b>FFHub não configurada.</b>\n"
            "Configure a FFHub no painel do dono antes de ativar."
        )
        return

    notify_chat_id = (
        message.chat.id
        if message.chat.type in {"group", "supergroup"}
        else None
    )
    notify_thread_id = (
        message.message_thread_id
        if notify_chat_id is not None
        else None
    )

    sub_id, created = await db.create_owner_ffhub_autolike_subscription(
        user_id=owner_id,
        game_id=uid,
        days_total=days,
        notify_chat_id=notify_chat_id,
        notify_thread_id=notify_thread_id,
    )
    if sub_id and (created or len(args) >= 3):
        await db.set_autolike_region(subscription_id=int(sub_id), region=region)

    if not created:
        extra = (
            "\n📣 <b>Notificações deste Auto Like vinculadas a este grupo.</b>"
            if notify_chat_id is not None
            else ""
        )
        await message.reply(
            "ℹ️ <b>Esse UID já possui Auto Like ativo.</b>\n\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"📄 Assinatura: <code>#{sub_id}</code>"
            f"{extra}\n\n"
            f"Use <code>/autolike status {uid}</code> para consultar."
        )
        return

    sub = await db.get_ffhub_autolike_status(uid)

    footer = (
        "📣 As entregas também serão avisadas neste grupo."
        if notify_chat_id is not None
        else "📩 As entregas serão avisadas no privado."
    )
    await send_fancy(
        bot,
        db,
        message.chat.id,
        activation_text(
            plan="500/1000+ LIKE",
            uid=uid,
            region=region,
            days=days,
            per_send="500 a 1.000 likes",
            next_send_ts=sub.get("next_send_at") if sub else None,
            footer=footer,
        ),
        reply_to_message_id=message.message_id,
        **(
            {"message_thread_id": message.message_thread_id}
            if message.is_topic_message and message.message_thread_id
            else {}
        ),
    )


# ---------- Remover ID do Auto Like 500 / 220 ----------
PLAN_LABEL = {"ffhub": "500", "autosystem": "220"}


def _plan_label(sub: dict) -> str:
    return PLAN_LABEL.get(str(sub.get("provider") or "ffhub"), "500")


def _removed_text(uid: str, removed: list[dict]) -> str:
    if not removed:
        return "ℹ️ Esse UID não está cadastrado em nenhum Auto Like ativo (500 ou 220)."
    plans = ", ".join(sorted({f"Auto Like {_plan_label(s)}" for s in removed}))
    return (
        "✅ <b>ID removido do Auto Like</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 UID: <code>{uid}</code>\n"
        f"📦 Plano: <b>{plans}</b>\n\n"
        "⛔ Esse ID não recebe mais likes automáticos."
    )


async def _removal_menu(db: Database) -> tuple[str, InlineKeyboardMarkup | None]:
    subs = await db.active_autolikes_all(limit=90)
    if not subs:
        return "🗑 <b>REMOVER ID DO AUTO LIKE</b>\n\nNenhum ID ativo no 500 ou 220.", None

    lines = [
        "🗑 <b>REMOVER ID DO AUTO LIKE</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "Toque no ID que deseja remover.",
        "Ou use: <code>/removerid UID</code>",
        "",
    ]
    rows = []
    for sub in subs:
        plan = _plan_label(sub)
        nick = str(sub.get("nickname") or "").strip()
        done = int(sub.get("sends_done") or 0)
        total = int(sub.get("days_total") or 0)
        lines.append(
            f"• <b>{plan}</b> · <code>{sub['game_id']}</code>"
            + (f" · {html.escape(nick)}" if nick else "")
            + f" · {done}/{total}"
        )
        label = f"❌ {plan} · {sub['game_id']}"
        if nick:
            label += f" · {nick[:16]}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"alrm:ask:{sub['id']}")]
        )
    rows.append([InlineKeyboardButton(text="Fechar", callback_data="alrm:close")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("removerid", "rmid", "removerautolike"))
async def cmd_remove_autolike_id(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        await message.reply("🔒 <b>Comando exclusivo dos donos.</b>")
        return

    arg = (command.args or "").strip()
    if arg:
        uid = clean_game_id(arg.split()[0])
        if not _valid_uid(uid):
            await message.reply("⚠️ UID inválido.")
            return
        removed = await db.cancel_autolike_by_uid(uid)
        await message.reply(_removed_text(uid, removed))
        return

    text, markup = await _removal_menu(db)
    await message.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("alrm:"))
async def cb_remove_autolike_id(
    call: CallbackQuery,
    config: Config,
    db: Database,
) -> None:
    if not call.from_user or call.from_user.id not in config.admin_ids:
        await call.answer("Exclusivo dos donos.", show_alert=True)
        return

    parts = (call.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "close":
        await call.answer()
        try:
            await call.message.delete()
        except Exception:  # noqa: BLE001
            pass
        return

    if action == "list":
        text, markup = await _removal_menu(db)
        await call.answer()
        await call.message.edit_text(text, reply_markup=markup)
        return

    try:
        sub_id = int(parts[2])
    except (IndexError, ValueError):
        await call.answer()
        return

    sub = await db.get_autolike_subscription(sub_id)
    if not sub or sub.get("status") != "active":
        await call.answer("Esse ID já não está ativo.", show_alert=True)
        text, markup = await _removal_menu(db)
        await call.message.edit_text(text, reply_markup=markup)
        return

    if action == "ask":
        await call.answer()
        await call.message.edit_text(
            "⚠️ <b>Confirmar remoção?</b>\n\n"
            f"📦 Plano: <b>Auto Like {_plan_label(sub)}</b>\n"
            f"🆔 UID: <code>{sub['game_id']}</code>\n"
            f"📆 Envios: <b>{int(sub.get('sends_done') or 0)}/{int(sub.get('days_total') or 0)}</b>\n\n"
            "Depois de remover, esse ID para de receber likes.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Sim, remover",
                            callback_data=f"alrm:do:{sub_id}",
                        ),
                        InlineKeyboardButton(
                            text="Voltar", callback_data="alrm:list"
                        ),
                    ]
                ]
            ),
        )
        return

    if action == "do":
        removed = await db.cancel_autolike_subscription(sub_id)
        await call.answer("ID removido." if removed else "Já estava removido.")
        await call.message.edit_text(
            _removed_text(str(sub["game_id"]), removed),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Remover outro ID", callback_data="alrm:list"
                        )
                    ]
                ]
            ),
        )
        return

    await call.answer()
