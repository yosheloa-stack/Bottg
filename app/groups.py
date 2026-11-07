"""Registro dos grupos onde o bot está e comando /grupos para o dono.

- Quando o bot entra/sai de um grupo, o Telegram avisa (my_chat_member) e
  o grupo é gravado/desativado no banco. Na entrada, o @YoshGGx recebe um
  aviso no privado com o link do grupo (quando dá para obter).
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
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from app.config import Config
from app.database import Database
from app.owner_auth import resolve_owner_id

logger = logging.getLogger(__name__)

router = Router(name="groups")

GROUP_TYPES = {"group", "supergroup"}
REFRESH_WORDS = {"atualizar", "verificar", "checar"}
# Recebe no privado um aviso sempre que alguém adiciona o bot em um grupo.
GROUP_ADD_NOTIFY_USERNAME = "YoshGGx"


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


async def _group_link(bot: Bot, chat: Any) -> str:
    """Link público do grupo; senão tenta gerar um convite (precisa ser admin)."""
    if chat.username:
        return f"https://t.me/{chat.username}"
    try:
        invite = await bot.create_chat_invite_link(chat.id, name="Aviso do bot")
        return invite.invite_link
    except Exception:  # noqa: BLE001
        pass
    try:
        full = await bot.get_chat(chat.id)
        if full.invite_link:
            return full.invite_link
    except Exception:  # noqa: BLE001
        pass
    return ""


async def _notify_group_added(
    bot: Bot,
    db: Database,
    config: Config,
    event: ChatMemberUpdated,
) -> None:
    """Avisa o @ configurado (e, se não achar, os donos) que o bot entrou num grupo."""
    chat = event.chat
    user = event.from_user
    link = await _group_link(bot, chat)
    try:
        members: int | None = await bot.get_chat_member_count(chat.id)
    except Exception:  # noqa: BLE001
        members = None

    who = "desconhecido"
    if user:
        who = html.escape(user.full_name or "")
        if user.username:
            who += f" (@{html.escape(user.username)})"
        who += f" · <code>{user.id}</code>"

    text = (
        "<b>BOT ADICIONADO EM UM GRUPO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Grupo: <b>{html.escape(chat.title or 'Sem nome')}</b>\n"
        f"ID: <code>{chat.id}</code>\n"
        + (f"Membros: <b>{members}</b>\n" if members is not None else "")
        + f"Adicionado por: {who}\n"
        + (
            f"Link: {html.escape(link)}"
            if link
            else "Link: indisponível (grupo privado e o bot não é admin)"
        )
    )

    targets: list[int] = []
    notify_id = await db.get_user_id_by_username(GROUP_ADD_NOTIFY_USERNAME)
    if notify_id is not None:
        targets.append(notify_id)
    else:
        logger.warning(
            "@%s ainda não iniciou o bot; aviso de grupo vai para os donos",
            GROUP_ADD_NOTIFY_USERNAME,
        )
        targets.extend(int(x) for x in config.admin_ids)

    for target in targets:
        try:
            await bot.send_message(target, text, disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao avisar %s sobre o grupo %s", target, chat.id)


@router.my_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION),
    F.chat.type.in_(GROUP_TYPES),
)
async def on_bot_added(
    event: ChatMemberUpdated,
    db: Database,
    bot: Bot,
    config: Config,
) -> None:
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
    await _notify_group_added(bot, db, config, event)


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


# ---------- /anunciar: envia um anúncio para todos os grupos e fixa ----------
# Dono -> aguardando o anúncio (True) ou anúncio pronto (chat_id, message_id).
_ad_waiting: set[int] = set()
_ad_ready: dict[int, tuple[int, int]] = {}


def _ad_confirm_markup(total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Enviar e fixar em {total} grupos",
                    callback_data="ad:send",
                )
            ],
            [InlineKeyboardButton(text="Cancelar", callback_data="ad:cancel")],
        ]
    )


async def _prepare_ad(message: Message, source: Message, db: Database) -> None:
    owner = message.from_user.id
    _ad_waiting.discard(owner)
    groups = await db.list_bot_groups(active_only=True)
    if not groups:
        await message.answer("Nenhum grupo registrado. Veja com <code>/grupos</code>.")
        return
    _ad_ready[owner] = (source.chat.id, source.message_id)
    await message.answer(
        "<b>ANÚNCIO PRONTO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Vai ser enviado em <b>{len(groups)} grupos</b> exatamente como a "
        "mensagem acima e fixado em cada um.\n"
        "Para fixar, o bot precisa ser admin com permissão de fixar.",
        reply_markup=_ad_confirm_markup(len(groups)),
    )


@router.message(Command("anunciar", "divulgar"))
async def cmd_announce(
    message: Message,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    if message.chat.type in GROUP_TYPES or not message.from_user:
        await message.reply("Use <code>/anunciar</code> no privado do bot.")
        return

    arg = (message.text or "").split(maxsplit=1)
    if len(arg) > 1 and arg[1].strip().lower() in {"cancelar", "cancel"}:
        _ad_waiting.discard(message.from_user.id)
        _ad_ready.pop(message.from_user.id, None)
        await message.answer("Anúncio cancelado.")
        return

    # Respondendo uma mensagem com /anunciar usa ela direto.
    if message.reply_to_message:
        await _prepare_ad(message, message.reply_to_message, db)
        return

    _ad_ready.pop(message.from_user.id, None)
    _ad_waiting.add(message.from_user.id)
    await message.answer(
        "<b>ANUNCIAR NOS GRUPOS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Envie agora o anúncio: imagem com legenda, só texto, vídeo ou GIF.\n"
        "Ele vai do jeito que você mandar e fica fixado nos grupos.\n\n"
        "<code>/anunciar cancelar</code> — desistir"
    )


@router.message(
    F.chat.type == "private",
    F.from_user.func(lambda u: u is not None and u.id in _ad_waiting),
    ~F.text.startswith("/"),
)
async def receive_announcement(message: Message, db: Database) -> None:
    if message.media_group_id:
        _ad_waiting.discard(message.from_user.id)
        await message.answer(
            "Álbum com várias fotos não dá para fixar como um anúncio só. "
            "Mande uma imagem com a legenda e use <code>/anunciar</code> de novo."
        )
        return
    await _prepare_ad(message, message, db)


@router.callback_query(F.data.in_({"ad:send", "ad:cancel"}))
async def cb_announce(
    call: CallbackQuery,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    if not call.from_user or call.from_user.id not in config.admin_ids:
        await call.answer("Exclusivo dos donos.", show_alert=True)
        return
    owner = call.from_user.id

    if call.data == "ad:cancel":
        _ad_ready.pop(owner, None)
        await call.answer("Cancelado.")
        await call.message.edit_text("Anúncio cancelado.")
        return

    ready = _ad_ready.pop(owner, None)
    if not ready:
        await call.answer(
            "Esse anúncio expirou. Use /anunciar de novo.", show_alert=True
        )
        return
    from_chat_id, message_id = ready

    await call.answer("Enviando...")
    groups = await db.list_bot_groups(active_only=True)
    await call.message.edit_text(f"Enviando para {len(groups)} grupos...")

    sent = pinned = 0
    failed: list[str] = []
    not_pinned: list[str] = []
    for group in groups:
        chat_id = int(group["chat_id"])
        name = group.get("title") or str(chat_id)
        try:
            try:
                copied = await bot.copy_message(chat_id, from_chat_id, message_id)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
                copied = await bot.copy_message(chat_id, from_chat_id, message_id)
            except TelegramMigrateToChat as exc:
                await db.deactivate_bot_group(chat_id)
                chat_id = exc.migrate_to_chat_id
                await db.upsert_bot_group(chat_id, group.get("title") or "", "")
                copied = await bot.copy_message(chat_id, from_chat_id, message_id)
        except TelegramForbiddenError:
            # O bot saiu ou foi removido do grupo.
            await db.deactivate_bot_group(chat_id)
            failed.append(name)
            continue
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao enviar anúncio para %s", chat_id)
            failed.append(name)
            continue

        sent += 1
        try:
            await bot.pin_chat_message(chat_id, copied.message_id)
            pinned += 1
        except Exception:  # noqa: BLE001
            logger.info("Sem permissão para fixar no grupo %s", chat_id)
            not_pinned.append(name)
        await asyncio.sleep(0.2)

    lines = [
        "<b>ANÚNCIO ENVIADO</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Enviado: <b>{sent}</b> grupos",
        f"Fixado: <b>{pinned}</b> grupos",
    ]
    if not_pinned:
        lines.append(
            "\nNão fixou (bot sem permissão de fixar):\n"
            + "\n".join(f"• {html.escape(n)}" for n in not_pinned[:30])
        )
    if failed:
        lines.append(
            "\nNão enviou:\n"
            + "\n".join(f"• {html.escape(n)}" for n in failed[:30])
        )
    text = "\n".join(lines)
    try:
        await call.message.edit_text(text[:4000])
    except Exception:  # noqa: BLE001
        await bot.send_message(owner, text[:4000])
