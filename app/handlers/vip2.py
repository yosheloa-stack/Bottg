"""VIP2: acesso exclusivo ao comando /like2 direto."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User

from app.autodelete import auto_delete
from app.config import Config
from app.database import Database
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
        "<code>/vip2 add ID 30</code>\n"
        "<code>/vip2 add ID 0</code> — permanente\n"
        "<code>/vip2 remove ID</code>\n"
        "<code>/vip2 status ID</code>\n"
        "<code>/vip2 list</code>\n"
        "<code>/liberarlike2 @usuario 5</code> — no grupo, libera envios"
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
        text = _vip2_text(vip, user_id)
        credits = await db.get_like2_credits(user_id)
        if credits:
            text += f"\n\n🎟️ Envios Like2 liberados: <b>{credits}</b>"
        await message.answer(text)
        return

    if not args:
        await message.answer(await _panel_text(db))
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
            f"📅 Validade: <b>{_fmt_expiry(int(vip['expires_at']))}</b>"
        )
        await _notify(
            bot,
            target,
            "🔥 <b>Seu VIP2 foi ativado!</b>\n\n"
            "Agora você pode usar <code>/like2 UID</code> nos grupos.\n"
            "🕒 Limite: <b>1 envio a cada 24 horas</b>.\n"
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
            "✅ VIP2 removido." if removed else "ℹ️ Esse ID não possui VIP2 ativo."
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
        await message.answer("\n".join(lines))
        return

    await message.answer("⚠️ Ação inválida. Use <code>/vip2</code>.")


def _user_label(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


async def _resolve_targets(
    message: Message, db: Database
) -> tuple[list[tuple[int, str]], list[str]]:
    """Pessoas marcadas (@username ou menção sem @) ou, sem marcação, a respondida."""
    text = message.text or message.caption or ""
    found: dict[int, str] = {}
    missing: list[str] = []
    for entity in message.entities or message.caption_entities or []:
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            user = entity.user
            await db.upsert_user(user.id, user.username, user.first_name)
            found.setdefault(int(user.id), _user_label(user))
        elif entity.type == MessageEntityType.MENTION:
            username = entity.extract_from(text).lstrip("@")
            row = await db.find_user_by_username(username)
            if row:
                found.setdefault(int(row["user_id"]), f"@{username}")
            else:
                missing.append(f"@{username}")

    reply = message.reply_to_message
    # Em grupos com tópicos, toda mensagem "responde" à criação do tópico; isso não conta.
    if (
        not found
        and not missing
        and reply
        and reply.from_user
        and not reply.from_user.is_bot
        and reply.forum_topic_created is None
    ):
        user = reply.from_user
        await db.upsert_user(user.id, user.username, user.first_name)
        found[int(user.id)] = _user_label(user)
    return list(found.items()), missing


@router.message(Command("liberarlike2", "addlike2", "darlike2"))
async def cmd_liberar_like2(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    """Dono libera envios de /like2 marcando as pessoas: /liberarlike2 @a @b 5."""
    if not message.from_user:
        return
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return

    numbers = [a for a in (command.args or "").split() if a.isdigit()]
    amount = int(numbers[-1]) if numbers else 1
    if amount < 1 or amount > 1000:
        reply = await message.answer("⚠️ Use de 1 a 1000 envios.")
        auto_delete(bot, message, reply)
        return

    targets, missing = await _resolve_targets(message, db)
    if not targets:
        if missing:
            text = (
                f"⚠️ Não encontrei {', '.join(missing)}. Peça para a pessoa dar /start "
                "no bot ou responda a uma mensagem dela com <code>/liberarlike2 QTD</code>."
            )
        else:
            text = (
                "Use: <code>/liberarlike2 @usuario 5</code>\n"
                "Pode marcar várias pessoas: <code>/liberarlike2 @a @b 3</code>\n"
                "Ou responda a uma mensagem da pessoa com <code>/liberarlike2 5</code>.\n"
                "Os envios somam ao saldo e descontam sozinhos a cada /like2."
            )
        reply = await message.answer(text)
        auto_delete(bot, message, reply)
        return

    lines = ["✅ <b>LIKE2 liberado</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for target, label in targets:
        total = await db.add_like2_credits(target, amount, owner_id)
        lines.append(f"👤 {label} — +{amount} (saldo: <b>{total}</b>)")
        await _notify(
            bot,
            target,
            "🔥 <b>Like2 liberado para você!</b>\n\n"
            f"🎟️ Envios recebidos: <b>{amount}</b>\n"
            f"📦 Saldo atual: <b>{total}</b>\n\n"
            "Use <code>/like2 SEU_ID</code> no grupo. Cada envio desconta 1 do saldo.",
        )
    if missing:
        lines.append(f"\n⚠️ Não encontrei: {', '.join(missing)}")
    reply = await message.answer("\n".join(lines))
    auto_delete(bot, message, reply)


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

