"""Registro dos grupos onde o bot está e comando /grupos para o dono.

- Quando o bot entra/sai de um grupo, o Telegram avisa (my_chat_member) e
  o grupo é gravado/desativado no banco.
- Grupos onde o bot já estava antes desta versão são registrados na primeira
  mensagem que o bot receber neles.
- /grupos lista os grupos; /grupos atualizar confere cada um no Telegram
  (nome, @, membros), remove os que o bot saiu e procura grupos antigos que
  apareceram em aluguéis, autolikes e pedidos.
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.filters import (
    JOIN_TRANSITION,
    LEAVE_TRANSITION,
    ChatMemberUpdatedFilter,
    Command,
)
from aiogram.types import ChatMemberUpdated, Message, TelegramObject

from app.config import Config
from app.database import Database
from app.owner_auth import resolve_owner_id

logger = logging.getLogger(__name__)

router = Router(name="groups")

GROUP_TYPES = {"group", "supergroup"}
REFRESH_WORDS = {"atualizar", "verificar", "checar"}


class GroupTrackerMiddleware(BaseMiddleware):
    """Registra grupos onde o bot já estava antes desta versão.

    Grava no banco só na primeira mensagem de cada grupo por execução.
    """

    def __init__(self) -> None:
        self._seen: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type in GROUP_TYPES
            and event.chat.id not in self._seen
        ):
            db: Database | None = data.get("db")
            if db is not None:
                try:
                    await db.upsert_bot_group(
                        event.chat.id,
                        event.chat.title or "",
                        event.chat.username or "",
                    )
                    self._seen.add(event.chat.id)
                except Exception:  # noqa: BLE001
                    logger.exception("Falha ao registrar grupo %s", event.chat.id)
        return await handler(event, data)


@router.my_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION),
    F.chat.type.in_(GROUP_TYPES),
)
async def on_bot_added(event: ChatMemberUpdated, db: Database) -> None:
    added_by = event.from_user.id if event.from_user else None
    await db.upsert_bot_group(
        event.chat.id,
        event.chat.title or "",
        event.chat.username or "",
        added_by,
    )
    logger.info(
        "Bot adicionado ao grupo %s (%s) por %s",
        event.chat.id,
        event.chat.title,
        added_by,
    )


@router.my_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION),
    F.chat.type.in_(GROUP_TYPES),
)
async def on_bot_removed(event: ChatMemberUpdated, db: Database) -> None:
    await db.deactivate_bot_group(event.chat.id)
    logger.info("Bot removido do grupo %s (%s)", event.chat.id, event.chat.title)


async def _check_group(bot: Bot, db: Database, chat_id: int) -> dict[str, Any] | None:
    """Confere o grupo no Telegram. Retorna None quando o bot não está mais nele."""
    try:
        try:
            chat = await bot.get_chat(chat_id)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            chat = await bot.get_chat(chat_id)
        except TelegramMigrateToChat as exc:
            # Grupo virou supergrupo: troca o ID.
            await db.deactivate_bot_group(chat_id)
            chat_id = exc.migrate_to_chat_id
            chat = await bot.get_chat(chat_id)
    except (TelegramForbiddenError, TelegramBadRequest):
        await db.deactivate_bot_group(chat_id)
        return None

    if chat.type not in GROUP_TYPES:
        return None
    try:
        members: int | None = await bot.get_chat_member_count(chat_id)
    except Exception:  # noqa: BLE001
        members = None
    await db.upsert_bot_group(chat_id, chat.title or "", chat.username or "")
    return {
        "chat_id": chat_id,
        "title": chat.title or "",
        "username": chat.username or "",
        "members": members,
    }


@router.message(Command("grupos"))
async def cmd_groups(
    message: Message,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    """Lista para o dono os grupos onde o bot está."""
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    if message.chat.type in GROUP_TYPES:
        await message.reply("Use <code>/grupos</code> no privado do bot.")
        return

    parts = (message.text or "").split(maxsplit=1)
    refresh = len(parts) > 1 and parts[1].strip().lower() in REFRESH_WORDS

    if refresh:
        saved = await db.list_bot_groups(active_only=True)
        ids = [int(g["chat_id"]) for g in saved]
        ids += sorted((await db.known_group_chat_ids()) - set(ids))
        await message.answer(f"Conferindo {len(ids)} grupos no Telegram...")
        groups: list[dict[str, Any]] = []
        for chat_id in ids:
            try:
                info = await _check_group(bot, db, chat_id)
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao conferir grupo %s", chat_id)
                continue
            if info is not None:
                groups.append(info)
            await asyncio.sleep(0.1)
    else:
        groups = await db.list_bot_groups(active_only=True)

    lines: list[str] = []
    for group in groups:
        extra = ""
        if group.get("username"):
            extra += f" · @{group['username']}"
        if group.get("members") is not None:
            extra += f" · {group['members']} membros"
        lines.append(
            f"{len(lines) + 1}. <b>{html.escape(group['title'] or 'Sem nome')}</b>"
            f"{html.escape(extra)}\n    <code>{group['chat_id']}</code>"
        )

    active, total = await db.count_bot_groups()
    header = (
        "<b>GRUPOS COM O BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Ativos: <b>{active}</b> (já passaram {total})\n"
    )
    footer = (
        ""
        if refresh
        else "\n<code>/grupos atualizar</code> — conferir no Telegram "
        "(nome, @, membros) e achar grupos antigos"
    )
    if not lines:
        await message.answer(header + "\nNenhum grupo registrado ainda." + footer)
        return

    # Telegram limita cada mensagem a 4096 caracteres.
    chunk = header + "\n"
    for line in lines:
        if len(chunk) + len(line) + 1 > 3900:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    await message.answer(chunk + footer)
