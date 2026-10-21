"""VIP2: acesso exclusivo ao comando /like2 direto."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Config
from app.database import Database
from app.handlers.vip import _parse_days, _resolve_target
from app.owner_auth import resolve_owner_id
from app.states import AdminVip2Add, AdminVip2Remove

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
        "🕒 Limite: <b>1 envio a cada 24 horas</b>\n"
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
        "Cada VIP2 pode fazer <b>1 envio a cada 24 horas</b>.\n"
        "Quem não tiver VIP2 é bloqueado antes da FFHub ser chamada.\n\n"
        "Comandos:\n"
        "<code>/addvip2 ID 30</code> — soma 30 dias\n"
        "<code>/addvip2 @usuario 30</code> — no grupo, marcando\n"
        "<code>/addvip2 30</code> — respondendo a mensagem da pessoa\n"
        "<code>/addvip2 ID 0</code> — permanente\n"
        "<code>/vip2 remove ID</code> (ou @usuario)\n"
        "<code>/vip2 status ID</code>\n"
        "<code>/vip2 list</code>\n\n"
        "<code>/vip2 ID 30</code> também funciona."
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

    resolved_owner_id = await resolve_owner_id(message, bot, config)
    user_id = resolved_owner_id or message.from_user.id
    is_owner = resolved_owner_id is not None
    args = (command.args or "").split()

    if not is_owner:
        vip = await db.get_vip2(user_id)
        await message.answer(_vip2_text(vip, user_id))
        return

    if not args:
        await message.answer(await _panel_text(db))
        return

    action = args[0].lower()

    if action in {"add", "adicionar"}:
        await _add_vip2(message, db, bot, user_id, args[1:])
        return

    if action in {"remove", "remover", "del"}:
        target, label = await _resolve_target(message, db, args[1:])
        if target is None:
            await message.answer(
                _not_found(label)
                if label
                else "Use: <code>/vip2 remove ID</code> ou <code>/vip2 remove @usuario</code>"
            )
            return
        removed = await db.remove_vip2(target)
        await message.answer(
            "✅ VIP2 removido." if removed else "ℹ️ Esse usuário não possui VIP2 ativo."
        )
        if removed:
            await _notify(bot, target, "ℹ️ <b>Seu acesso VIP2 foi encerrado.</b>")
        return

    if action in {"status", "ver"}:
        target, label = await _resolve_target(message, db, args[1:])
        if target is None and label:
            await message.answer(_not_found(label))
            return
        target = target or user_id
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
        await message.answer("\n".join(lines))
        return

    # Sem palavra de ação: "/vip2 ID 30", "/vip2 @usuario 30", "/vip2 Nome 1" = adicionar.
    await _add_vip2(message, db, bot, user_id, args)


@router.message(Command("addvip2"))
async def cmd_addvip2(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    """Dono adiciona VIP2: /addvip2 ID 30, /addvip2 @usuario 30 ou respondendo alguém."""
    if not message.from_user:
        return
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    await _add_vip2(message, db, bot, owner_id, (command.args or "").split())


def _usage_add() -> str:
    return (
        "Use: <code>/addvip2 ID 30</code> ou <code>/addvip2 @usuario 30</code>\n"
        "Ou responda a uma mensagem da pessoa com <code>/addvip2 30</code>.\n"
        "Os dias somam ao VIP2 atual. <code>0</code> = permanente."
    )


def _not_found(label: str) -> str:
    return (
        f"⚠️ Não encontrei {label}. Use o Telegram ID, marque a pessoa pela lista "
        "de menções ou responda a uma mensagem dela.\n"
        "Se ela nunca falou com o bot, peça para dar /start."
    )


async def _add_vip2(
    message: Message, db: Database, bot: Bot, owner_id: int, args: list[str]
) -> None:
    days = _parse_days(args)
    if days > 3650:
        await message.answer("⚠️ Use de 0 a 3650 dias. 0 = permanente.")
        return

    target, label = await _resolve_target(message, db, args)
    if target is None:
        await message.answer(_not_found(label) if label else _usage_add())
        return

    vip = await db.extend_vip2(target, days, owner_id)
    expires_at = int(vip["expires_at"])
    shown = label if label and label != str(target) else ""
    await message.answer(
        "✅ <b>VIP2 adicionado</b>\n\n"
        f"👤 {shown + ' ' if shown else ''}(<code>{target}</code>)\n"
        f"➕ {'Permanente' if days == 0 else f'{days} dias'}\n"
        f"📅 Validade: <b>{_fmt_expiry(expires_at)}</b>\n"
        f"⏳ Restante: <b>{_remaining(expires_at)}</b>"
    )
    await _notify(
        bot,
        target,
        "🔥 <b>Seu VIP2 foi ativado!</b>\n\n"
        "Agora você pode usar <code>/like2 UID</code> nos grupos.\n"
        "🕒 Limite: <b>1 envio a cada 24 horas</b>.\n"
        f"📅 Validade: <b>{_fmt_expiry(expires_at)}</b>",
    )


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
        f"📅 {_fmt_expiry(int(vip['expires_at']))}"
    )
    await _notify(
        bot,
        target,
        "🔥 <b>Seu VIP2 foi ativado!</b>\n\n"
        "Você agora pode usar <code>/like2 UID</code> nos grupos.\n"
        "🕒 Limite: <b>1 envio a cada 24 horas</b>.\n"
        f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>",
    )


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
        "✅ VIP2 removido." if removed else "ℹ️ Esse ID não possui VIP2 ativo."
    )
    if removed:
        await _notify(bot, target, "ℹ️ <b>Seu acesso VIP2 foi encerrado.</b>")

