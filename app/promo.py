"""Divulgação do bot em grupos.

Qualquer pessoa pode adicionar o bot ao próprio grupo. A partir daí o bot:

- manda uma mensagem de boas-vindas com os botões 🛒 Loja e ➕ Adicionar ao grupo;
- coloca esses botões nas respostas do /like em grupos;
- responde /loja (e /divulgar) com a vitrine;
- publica, de tempos em tempos, uma divulgação em cada grupo (configurável
  pelo dono com /divulgacao).

Os botões levam ao privado do bot (?start=shop), onde fica o menu de compras.
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import quote

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

router = Router(name="promo")

GROUP_TYPES = {"group", "supergroup"}

SETTING_ENABLED = "PROMO_ENABLED"
SETTING_INTERVAL = "PROMO_INTERVAL_HOURS"
SETTING_TEXT = "PROMO_TEXT"

DEFAULT_INTERVAL_HOURS = 12
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 168

DEFAULT_PROMO_TEXT = (
    "🔥 <b>{shop}</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "❤️ <b>Auto Like 220</b> e 💎 <b>Auto Like 500</b> todos os dias na sua conta\n"
    "🎟️ <b>Passe de Elite</b> com entrega automática\n"
    "💠 Pagamento por <b>PIX</b> com confirmação na hora\n\n"
    "👇 Toque em <b>Loja</b> para comprar ou adicione o bot no seu grupo."
)

GROUP_WELCOME = (
    "👋 <b>Olá, {title}!</b>\n\n"
    "Eu sou o <b>{shop}</b>, o bot de likes e itens de Free Fire.\n\n"
    "❤️ <code>/like SEU_ID</code> — envio de likes\n"
    "🛒 <code>/loja</code> — ver os produtos\n\n"
    "🤖 Para liberar o <code>/like</code> neste grupo, alugue o bot na Loja.\n"
    "Compras são feitas no meu privado pelo botão abaixo. 👇"
)

_username_cache: dict[int, str] = {}


async def _bot_username(bot: Bot) -> str:
    cached = _username_cache.get(bot.id)
    if cached:
        return cached
    me = await bot.get_me()
    username = me.username or ""
    if username:
        _username_cache[bot.id] = username
    return username


async def store_markup(bot: Bot) -> InlineKeyboardMarkup | None:
    """Botões 🛒 Loja + ➕ Adicionar em grupo."""
    try:
        username = await _bot_username(bot)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao obter username do bot para os botões da loja")
        return None
    if not username:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Loja",
                    url=f"https://t.me/{username}?start=shop",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="➕ Adicionar em grupo",
                    url=f"https://t.me/{username}?startgroup=true",
                ),
            ],
        ]
    )


async def group_store_markup(message: Message) -> InlineKeyboardMarkup | None:
    """Botões da loja só para respostas dentro de grupos."""
    if message.chat.type not in GROUP_TYPES:
        return None
    return await store_markup(message.bot)


async def _promo_text(db: Database, config: Config) -> str:
    custom = (await db.get_owner_setting(SETTING_TEXT) or "").strip()
    if custom:
        return custom
    return DEFAULT_PROMO_TEXT.format(shop=html.escape(config.shop_name))


async def _promo_enabled(db: Database) -> bool:
    raw = (await db.get_owner_setting(SETTING_ENABLED) or "1").strip()
    return raw != "0"


async def _promo_interval_hours(db: Database) -> int:
    raw = (await db.get_owner_setting(SETTING_INTERVAL) or "").strip()
    try:
        hours = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_HOURS
    return min(MAX_INTERVAL_HOURS, max(MIN_INTERVAL_HOURS, hours))


# ----- Registro dos grupos -----


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
                    await db.upsert_promo_group(event.chat.id, event.chat.title or "")
                    self._seen.add(event.chat.id)
                except Exception:  # noqa: BLE001
                    logger.exception("Falha ao registrar grupo %s", event.chat.id)
        return await handler(event, data)


@router.my_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION),
    F.chat.type.in_(GROUP_TYPES),
)
async def on_bot_added(
    event: ChatMemberUpdated,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    added_by = event.from_user.id if event.from_user else None
    await db.upsert_promo_group(event.chat.id, event.chat.title or "", added_by)
    logger.info(
        "Bot adicionado ao grupo %s (%s) por %s",
        event.chat.id,
        event.chat.title,
        added_by,
    )
    try:
        await bot.send_message(
            event.chat.id,
            GROUP_WELCOME.format(
                title=html.escape(event.chat.title or "pessoal"),
                shop=html.escape(config.shop_name),
            ),
            reply_markup=await store_markup(bot),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar boas-vindas no grupo %s", event.chat.id)


@router.my_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION),
    F.chat.type.in_(GROUP_TYPES),
)
async def on_bot_removed(event: ChatMemberUpdated, db: Database) -> None:
    await db.deactivate_promo_group(event.chat.id)
    logger.info("Bot removido do grupo %s (%s)", event.chat.id, event.chat.title)


# ----- Comandos -----


@router.message(Command("loja", "shop"))
async def cmd_store(
    message: Message,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    await message.answer(
        await _promo_text(db, config),
        reply_markup=await store_markup(bot),
    )


@router.message(Command("divulgar"))
async def cmd_share(
    message: Message,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    if message.chat.type in GROUP_TYPES:
        await cmd_store(message, db, config, bot)
        return

    username = await _bot_username(bot)
    if not username:
        await message.answer("⚠️ Não consegui gerar o link agora. Tente novamente.")
        return

    bot_link = f"https://t.me/{username}"
    share_text = (
        f"🔥 {config.shop_name}: likes e Passe de Elite para Free Fire "
        "com entrega automática. Confira:"
    )
    share_url = (
        f"https://t.me/share/url?url={quote(bot_link)}&text={quote(share_text)}"
    )
    await message.answer(
        "📣 <b>DIVULGUE O BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "➕ Adicione o bot no seu grupo: seus membros ganham o comando "
        "<code>/like</code> e o botão da loja.\n"
        "📤 Ou compartilhe o link com seus amigos.\n\n"
        f"🔗 <code>{bot_link}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Adicionar ao meu grupo",
                        url=f"{bot_link}?startgroup=promo",
                    )
                ],
                [InlineKeyboardButton(text="Compartilhar", url=share_url)],
            ]
        ),
    )


@router.message(Command("divulgacao"))
async def cmd_promo_settings(
    message: Message,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    """Painel do dono para a divulgação automática nos grupos."""
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return

    parts = (message.text or "").split(maxsplit=2)
    action = parts[1].strip().lower() if len(parts) > 1 else "status"

    if action in {"on", "ligar", "ativar"}:
        await db.set_owner_setting(SETTING_ENABLED, "1")
        await message.reply("✅ Divulgação automática nos grupos <b>ativada</b>.")
        return

    if action in {"off", "desligar", "desativar"}:
        await db.set_owner_setting(SETTING_ENABLED, "0")
        await message.reply("⏸️ Divulgação automática nos grupos <b>desativada</b>.")
        return

    if action in {"horas", "intervalo"}:
        raw = parts[2].strip() if len(parts) > 2 else ""
        try:
            hours = int(raw)
        except ValueError:
            hours = 0
        if not MIN_INTERVAL_HOURS <= hours <= MAX_INTERVAL_HOURS:
            await message.reply(
                f"Use: <code>/divulgacao horas N</code> "
                f"(de {MIN_INTERVAL_HOURS} a {MAX_INTERVAL_HOURS})."
            )
            return
        await db.set_owner_setting(SETTING_INTERVAL, str(hours))
        await message.reply(f"✅ Divulgação a cada <b>{hours}h</b> em cada grupo.")
        return

    if action == "texto":
        # html_text preserva negrito/itálico/links que o dono usou na mensagem.
        html_parts = (message.html_text or "").split(maxsplit=2)
        new_text = html_parts[2].strip() if len(html_parts) > 2 else ""
        if not new_text:
            await message.reply(
                "Use: <code>/divulgacao texto SUA MENSAGEM</code>\n"
                "Para voltar ao texto padrão: <code>/divulgacao texto padrao</code>"
            )
            return
        if new_text.lower() in {"padrao", "padrão", "reset"}:
            await db.set_owner_setting(SETTING_TEXT, "")
            await message.reply("✅ Texto da divulgação voltou ao padrão.")
            return
        await db.set_owner_setting(SETTING_TEXT, new_text)
        await message.reply(
            "✅ Texto da divulgação atualizado. Prévia:\n\n" + new_text,
            reply_markup=await store_markup(bot),
        )
        return

    if action in {"agora", "enviar"}:
        await db.reset_promo_schedule()
        await message.reply(
            "🚀 A divulgação vai sair em todos os grupos ativos no próximo minuto."
        )
        return

    enabled = await _promo_enabled(db)
    hours = await _promo_interval_hours(db)
    active, total = await db.count_promo_groups()
    custom = bool((await db.get_owner_setting(SETTING_TEXT) or "").strip())
    await message.reply(
        "📣 <b>DIVULGAÇÃO NOS GRUPOS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: <b>{'Ativa ✅' if enabled else 'Desativada ⏸️'}</b>\n"
        f"Intervalo: <b>a cada {hours}h</b>\n"
        f"Texto: <b>{'personalizado' if custom else 'padrão'}</b>\n"
        f"Grupos com o bot: <b>{active}</b> (já passaram {total})\n\n"
        "<code>/divulgacao on</code> | <code>/divulgacao off</code>\n"
        "<code>/divulgacao horas 12</code>\n"
        "<code>/divulgacao texto SUA MENSAGEM</code>\n"
        "<code>/divulgacao agora</code> — enviar em todos agora"
    )


# ----- Envio periódico -----


async def _send_promo(
    bot: Bot,
    db: Database,
    chat_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None,
) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramMigrateToChat as exc:
        # Grupo virou supergrupo: troca o ID e tenta no novo.
        await db.deactivate_promo_group(chat_id)
        await db.upsert_promo_group(exc.migrate_to_chat_id, "")
        await bot.send_message(exc.migrate_to_chat_id, text, reply_markup=markup)
        await db.mark_promo_sent(exc.migrate_to_chat_id)
        return
    except TelegramForbiddenError:
        # Bot foi removido/bloqueado no grupo.
        await db.deactivate_promo_group(chat_id)
        return
    except TelegramBadRequest as exc:
        if "chat not found" in str(exc).lower():
            await db.deactivate_promo_group(chat_id)
            return
        # Ex.: grupo restringiu mensagens. Tenta de novo só no próximo intervalo.
        logger.warning("Divulgação não enviada no grupo %s: %s", chat_id, exc)
    await db.mark_promo_sent(chat_id)


async def watch_promo(bot: Bot, db: Database, config: Config) -> None:
    """Publica a divulgação em cada grupo ativo a cada N horas."""
    while True:
        try:
            if await _promo_enabled(db):
                interval = await _promo_interval_hours(db) * 3600
                due = await db.due_promo_groups(interval, limit=30)
                if due:
                    text = await _promo_text(db, config)
                    markup = await store_markup(bot)
                    for group in due:
                        try:
                            await _send_promo(
                                bot, db, int(group["chat_id"]), text, markup
                            )
                        except TelegramRetryAfter as exc:
                            await asyncio.sleep(exc.retry_after + 1)
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Falha na divulgação do grupo %s", group["chat_id"]
                            )
                            await db.mark_promo_sent(int(group["chat_id"]))
                        # Respeita o limite de mensagens do Telegram.
                        await asyncio.sleep(1)
        except Exception:  # noqa: BLE001
            logger.exception("Erro no agendador de divulgação")

        await asyncio.sleep(60)
