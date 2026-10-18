"""Gerenciamento de VIP e consulta de assinatura."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.enums import MessageEntityType
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User

from app.config import Config
from app.database import Database
from app.owner_auth import resolve_owner_id
from app.states import AdminVipAdd, AdminVipRemove

router = Router(name="vip")
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


def _vip_text(vip: dict | None, user_id: int, *, owner: bool = False) -> str:
    if owner:
        return (
            "👑 <b>ACESSO DE DONO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            "♾️ Comandos sem limite\n"
            "✅ Acesso completo aos comandos do bot"
        )
    if not vip:
        return (
            "💎 <b>VIP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "❌ Você não possui VIP ativo.\n\n"
            "Usuários VIP podem usar o comando <code>/like</code> sem o limite de 24 horas."
        )
    expires_at = int(vip.get("expires_at") or 0)
    return (
        "💎 <b>VIP ATIVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        "♾️ <code>/like</code> sem limite de 24h\n"
        f"📅 Validade: <b>{_fmt_expiry(expires_at)}</b>\n"
        f"⏳ Restante: <b>{_remaining(expires_at)}</b>"
    )


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception:
        # O usuário pode nunca ter iniciado o bot ou ter bloqueado mensagens.
        pass


async def _panel_text(db: Database) -> str:
    vips = await db.active_vips()
    return (
        "💎 <b>GERENCIAR VIP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 VIPs ativos: <b>{len(vips)}</b>\n\n"
        "VIP tem <b>/like ilimitado</b>, sem espera de 24 horas.\n"
        "VIP com prazo expira automaticamente.\n\n"
        "Também pode usar:\n"
        "<code>/vip add ID 30</code>\n"
        "<code>/vip add ID 0</code> — permanente\n"
        "<code>/vip remove ID</code>\n"
        "<code>/vip status ID</code>\n"
        "<code>/vip list</code>\n"
        "<code>/addvip @usuario 30</code> — no grupo, soma dias"
    )


@router.message(Command("vip"))
async def cmd_vip(
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

    # Usuário comum consulta o próprio VIP.
    if not is_owner:
        vip = await db.get_vip(user_id)
        await message.answer(_vip_text(vip, user_id))
        return

    # Dono sem argumentos abre o gerenciador.
    if not args:
        await message.answer(await _panel_text(db))
        return

    action = args[0].lower()

    if action in {"add", "adicionar"}:
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(
                "Use: <code>/vip add TELEGRAM_ID DIAS</code>\n"
                "Ex.: <code>/vip add 123456789 30</code>\n"
                "Use <code>0</code> dias para VIP permanente."
            )
            return
        target = int(args[1])
        try:
            days = int(args[2]) if len(args) >= 3 else 30
        except ValueError:
            await message.answer("⚠️ Dias inválidos. Ex.: <code>/vip add 123456789 30</code>")
            return
        if days < 0 or days > 3650:
            await message.answer("⚠️ Use de 0 a 3650 dias. 0 = permanente.")
            return

        vip = await db.set_vip(target, days, user_id)
        await message.answer(
            "✅ <b>VIP ativado</b>\n\n"
            f"🆔 ID: <code>{target}</code>\n"
            f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>"
        )
        await _notify(
            bot,
            target,
            "💎 <b>Seu VIP foi ativado!</b>\n\n"
            "Agora você pode usar <code>/like</code> sem o limite de 24 horas.\n"
            f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>",
        )
        return

    if action in {"remove", "remover", "del"}:
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("Use: <code>/vip remove TELEGRAM_ID</code>")
            return
        target = int(args[1])
        removed = await db.remove_vip(target)
        await message.answer(
            ("✅ VIP removido." if removed else "ℹ️ Esse ID não possui VIP ativo.")
        )
        if removed:
            await _notify(bot, target, "ℹ️ <b>Seu acesso VIP foi encerrado.</b>")
        return

    if action in {"status", "ver"}:
        target = int(args[1]) if len(args) >= 2 and args[1].isdigit() else user_id
        if _owner(target, config):
            await message.answer(_vip_text(None, target, owner=True))
            return
        vip = await db.get_vip(target)
        await message.answer(_vip_text(vip, target))
        return

    if action in {"list", "lista"}:
        vips = await db.active_vips()
        if not vips:
            await message.answer("💎 <b>VIPs ativos</b>\n\nNenhum VIP cadastrado.")
            return
        lines = ["💎 <b>VIPs ativos</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for vip in vips[:50]:
            uid = int(vip["user_id"])
            exp = int(vip.get("expires_at") or 0)
            lines.append(f"• <code>{uid}</code> — {_fmt_expiry(exp)}")
        if len(vips) > 50:
            lines.append(f"\n… e mais {len(vips) - 50}.")
        await message.answer("\n".join(lines))
        return

    await message.answer(
        "⚠️ Ação inválida. Use <code>/vip</code> para abrir o gerenciamento."
    )


async def _resolve_target(message: Message, db: Database) -> tuple[int | None, str]:
    """Descobre o alvo do /addvip: menção (@username ou sem @) ou resposta à mensagem."""
    text = message.text or message.caption or ""
    for entity in message.entities or message.caption_entities or []:
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            user = entity.user
            await db.upsert_user(user.id, user.username, user.first_name)
            return int(user.id), _user_label(user)
        if entity.type == MessageEntityType.MENTION:
            username = entity.extract_from(text).lstrip("@")
            found = await db.find_user_by_username(username)
            if found:
                return int(found["user_id"]), f"@{username}"
            return None, f"@{username}"

    reply = message.reply_to_message
    # Em grupos com tópicos, toda mensagem "responde" à criação do tópico; isso não conta.
    if (
        reply
        and reply.from_user
        and not reply.from_user.is_bot
        and reply.forum_topic_created is None
    ):
        user = reply.from_user
        await db.upsert_user(user.id, user.username, user.first_name)
        return int(user.id), _user_label(user)
    return None, ""


def _user_label(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


@router.message(Command("addvip"))
async def cmd_addvip(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    """Dono adiciona VIP marcando ou respondendo alguém: /addvip @usuario 30."""
    if not message.from_user:
        return
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return

    numbers = [a for a in (command.args or "").split() if a.isdigit()]
    days = int(numbers[-1]) if numbers else 30
    if days > 3650:
        await message.answer("⚠️ Use de 0 a 3650 dias. 0 = permanente.")
        return

    target, label = await _resolve_target(message, db)
    if target is None:
        if label:
            await message.answer(
                f"⚠️ Não encontrei {label}. Peça para a pessoa dar /start no bot "
                "ou responda a uma mensagem dela com <code>/addvip DIAS</code>."
            )
        else:
            await message.answer(
                "Use: <code>/addvip @usuario 30</code>\n"
                "Ou responda a uma mensagem da pessoa com <code>/addvip 30</code>.\n"
                "Os dias somam ao VIP atual. <code>0</code> = permanente."
            )
        return

    vip = await db.extend_vip(target, days, owner_id)
    expires_at = int(vip["expires_at"])
    await message.answer(
        "✅ <b>VIP adicionado</b>\n\n"
        f"👤 {label} (<code>{target}</code>)\n"
        f"➕ {'Permanente' if days == 0 else f'{days} dias'}\n"
        f"📅 Validade: <b>{_fmt_expiry(expires_at)}</b>\n"
        f"⏳ Restante: <b>{_remaining(expires_at)}</b>"
    )
    await _notify(
        bot,
        target,
        "💎 <b>Seu VIP foi ativado!</b>\n\n"
        "Agora você pode usar <code>/like</code> sem o limite de 24 horas.\n"
        f"📅 Validade: <b>{_fmt_expiry(expires_at)}</b>",
    )


@router.message(AdminVipAdd.waiting_value)
async def vip_add_receive(
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

    vip = await db.set_vip(target, days, message.from_user.id)
    await state.clear()
    await message.answer(
        "✅ <b>VIP ativado</b>\n\n"
        f"🆔 <code>{target}</code>\n"
        f"📅 {_fmt_expiry(int(vip['expires_at']))}"
    )
    await _notify(
        bot,
        target,
        "💎 <b>Seu VIP foi ativado!</b>\n\n"
        "Você agora pode usar <code>/like</code> sem limite de 24 horas.\n"
        f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>",
    )


@router.message(AdminVipRemove.waiting_value)
async def vip_remove_receive(
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
    removed = await db.remove_vip(target)
    await state.clear()
    await message.answer(
        "✅ VIP removido." if removed else "ℹ️ Esse ID não possui VIP ativo."
    )
    if removed:
        await _notify(bot, target, "ℹ️ <b>Seu acesso VIP foi encerrado.</b>")

