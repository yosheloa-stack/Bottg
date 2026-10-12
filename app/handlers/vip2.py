"""VIP2: acesso exclusivo ao comando /like2 direto."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Config
from app.database import Database
from app.keyboards.inline import vip2_admin_panel
from app.states import AdminVip2Add, AdminVip2Remove
from app.ui import edit_screen

router = Router(name="vip2")
TZ_BR = ZoneInfo("America/Sao_Paulo")


def _owner(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


def _fmt_expiry(expires_at: int) -> str:
    if not expires_at:
        return "Permanente"
    return datetime.fromtimestamp(expires_at, TZ_BR).strftime("%d/%m/%Y às %H:%M")


def _remaining(expires_at: int) -> str:
    if not expires_at:
        return "Ilimitado"
    seconds = max(0, expires_at - int(time.time()))
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h"


def _vip2_text(vip: dict | None, user_id: int, *, owner: bool = False) -> str:
    if owner:
        return (
            "👑 <b>DONO • VIP2 LIBERADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            "🔥 <code>/like2 UID</code> liberado sem limite\n"
            "✅ Acesso completo de dono"
        )

    if not vip:
        return (
            "🔥 <b>VIP2</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "❌ Você não possui VIP2 ativo.\n\n"
            "O VIP2 é o acesso exclusivo ao comando:\n"
            "<code>/like2 UID</code>\n\n"
            "Sem VIP2, nenhuma requisição de Like2 é enviada."
        )

    expires_at = int(vip.get("expires_at") or 0)
    return (
        "🔥 <b>VIP2 ATIVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        "✅ <code>/like2 UID</code> liberado\n"
        "✅ Pode usar em grupos\n"
        f"📅 Validade: <b>{_fmt_expiry(expires_at)}</b>\n"
        f"⏳ Restante: <b>{_remaining(expires_at)}</b>"
    )


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception:
        pass


async def _panel_text(db: Database) -> str:
    vips = await db.active_vip2s()
    return (
        "🔥 <b>GERENCIAR VIP2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 VIP2 ativos: <b>{len(vips)}</b>\n\n"
        "VIP2 libera exclusivamente o comando <code>/like2 UID</code>.\n"
        "Quem não tiver VIP2 é bloqueado antes da FFHub ser chamada.\n\n"
        "Comandos:\n"
        "<code>/vip2 add ID 30</code>\n"
        "<code>/vip2 add ID 0</code> — permanente\n"
        "<code>/vip2 remove ID</code>\n"
        "<code>/vip2 status ID</code>\n"
        "<code>/vip2 list</code>"
    )


@router.message(Command("vip2"))
async def cmd_vip2(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    if not message.from_user:
        return

    user_id = message.from_user.id
    is_owner = _owner(user_id, config)
    args = (command.args or "").split()

    if not is_owner:
        vip = await db.get_vip2(user_id)
        await message.answer(_vip2_text(vip, user_id))
        return

    if not args:
        await message.answer(await _panel_text(db), reply_markup=vip2_admin_panel())
        return

    action = args[0].lower()

    if action in {"add", "adicionar"}:
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(
                "Use: <code>/vip2 add TELEGRAM_ID DIAS</code>\n"
                "Ex.: <code>/vip2 add 123456789 30</code>\n"
                "Use <code>0</code> para permanente."
            )
            return

        target = int(args[1])
        try:
            days = int(args[2]) if len(args) >= 3 else 30
        except ValueError:
            await message.answer("⚠️ Dias inválidos.")
            return

        if days < 0 or days > 3650:
            await message.answer("⚠️ Use de 0 a 3650 dias. 0 = permanente.")
            return

        vip = await db.set_vip2(target, days, user_id)
        await message.answer(
            "✅ <b>VIP2 ativado</b>\n\n"
            f"🆔 ID: <code>{target}</code>\n"
            f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>",
            reply_markup=vip2_admin_panel(),
        )
        await _notify(
            bot,
            target,
            "🔥 <b>Seu VIP2 foi ativado!</b>\n\n"
            "Agora você pode usar <code>/like2 UID</code> nos grupos.\n"
            f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>",
        )
        return

    if action in {"remove", "remover", "del"}:
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("Use: <code>/vip2 remove TELEGRAM_ID</code>")
            return
        target = int(args[1])
        removed = await db.remove_vip2(target)
        await message.answer(
            "✅ VIP2 removido." if removed else "ℹ️ Esse ID não possui VIP2 ativo.",
            reply_markup=vip2_admin_panel(),
        )
        if removed:
            await _notify(bot, target, "ℹ️ <b>Seu acesso VIP2 foi encerrado.</b>")
        return

    if action in {"status", "ver"}:
        target = int(args[1]) if len(args) >= 2 and args[1].isdigit() else user_id
        if _owner(target, config):
            await message.answer(_vip2_text(None, target, owner=True))
            return
        vip = await db.get_vip2(target)
        await message.answer(_vip2_text(vip, target))
        return

    if action in {"list", "lista"}:
        vips = await db.active_vip2s()
        if not vips:
            await message.answer("🔥 <b>VIP2 ativos</b>\n\nNenhum VIP2 cadastrado.")
            return

        lines = ["🔥 <b>VIP2 ativos</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for vip in vips[:50]:
            uid = int(vip["user_id"])
            exp = int(vip.get("expires_at") or 0)
            lines.append(f"• <code>{uid}</code> — {_fmt_expiry(exp)}")
        if len(vips) > 50:
            lines.append(f"\n… e mais {len(vips) - 50}.")
        await message.answer("\n".join(lines), reply_markup=vip2_admin_panel())
        return

    await message.answer("⚠️ Ação inválida. Use <code>/vip2</code>.")


@router.callback_query(F.data == "admin:vip2")
async def cb_vip2_panel(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _owner(query.from_user.id, config):
        return await query.answer("Sem permissão.", show_alert=True)
    await edit_screen(query, await _panel_text(db), vip2_admin_panel())
    await query.answer()


@router.callback_query(F.data == "vip2:add")
async def cb_vip2_add(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not _owner(query.from_user.id, config):
        return await query.answer("Sem permissão.", show_alert=True)
    await state.set_state(AdminVip2Add.waiting_value)
    await edit_screen(
        query,
        "➕ <b>Adicionar VIP2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Envie o <b>ID do Telegram</b> e os <b>dias</b>.\n\n"
        "Exemplo: <code>123456789 30</code>\n"
        "Use <code>0</code> para permanente.",
        vip2_admin_panel(),
    )
    await query.answer()


@router.message(AdminVip2Add.waiting_value)
async def vip2_add_receive(
    message: Message,
    state: FSMContext,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    if not message.from_user or not _owner(message.from_user.id, config):
        return

    parts = (message.text or "").split()
    if not parts or not parts[0].isdigit():
        await message.answer("⚠️ Envie assim: <code>TELEGRAM_ID DIAS</code>")
        return

    target = int(parts[0])
    try:
        days = int(parts[1]) if len(parts) > 1 else 30
    except ValueError:
        await message.answer("⚠️ Dias inválidos.")
        return

    if days < 0 or days > 3650:
        await message.answer("⚠️ Use de 0 a 3650 dias. 0 = permanente.")
        return

    vip = await db.set_vip2(target, days, message.from_user.id)
    await state.clear()
    await message.answer(
        "✅ <b>VIP2 ativado</b>\n\n"
        f"🆔 <code>{target}</code>\n"
        f"📅 {_fmt_expiry(int(vip['expires_at']))}",
        reply_markup=vip2_admin_panel(),
    )
    await _notify(
        bot,
        target,
        "🔥 <b>Seu VIP2 foi ativado!</b>\n\n"
        "Você agora pode usar <code>/like2 UID</code> nos grupos.\n"
        f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>",
    )


@router.callback_query(F.data == "vip2:remove")
async def cb_vip2_remove(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not _owner(query.from_user.id, config):
        return await query.answer("Sem permissão.", show_alert=True)
    await state.set_state(AdminVip2Remove.waiting_value)
    await edit_screen(
        query,
        "➖ <b>Remover VIP2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Envie o ID do Telegram que deseja remover.",
        vip2_admin_panel(),
    )
    await query.answer()


@router.message(AdminVip2Remove.waiting_value)
async def vip2_remove_receive(
    message: Message,
    state: FSMContext,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    if not message.from_user or not _owner(message.from_user.id, config):
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Envie somente o ID numérico do Telegram.")
        return

    target = int(raw)
    removed = await db.remove_vip2(target)
    await state.clear()
    await message.answer(
        "✅ VIP2 removido." if removed else "ℹ️ Esse ID não possui VIP2 ativo.",
        reply_markup=vip2_admin_panel(),
    )
    if removed:
        await _notify(bot, target, "ℹ️ <b>Seu acesso VIP2 foi encerrado.</b>")


@router.callback_query(F.data == "vip2:list")
async def cb_vip2_list(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _owner(query.from_user.id, config):
        return await query.answer("Sem permissão.", show_alert=True)

    vips = await db.active_vip2s()
    if not vips:
        text = "🔥 <b>VIP2 ativos</b>\n\nNenhum VIP2 cadastrado."
    else:
        lines = ["🔥 <b>VIP2 ativos</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for vip in vips[:40]:
            uid = int(vip["user_id"])
            exp = int(vip.get("expires_at") or 0)
            lines.append(f"• <code>{uid}</code> — {_fmt_expiry(exp)}")
        if len(vips) > 40:
            lines.append(f"\n… e mais {len(vips) - 40}.")
        text = "\n".join(lines)

    await edit_screen(query, text, vip2_admin_panel())
    await query.answer()
