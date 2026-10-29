"""Aplica o filtro de texto (sem emojis + idioma do usuário) em tudo que o bot envia."""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import (
    ForceReply,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    TelegramObject,
)

from app.i18n import DEFAULT_LANG, LANGUAGES, user_lang
from app.textfilter import render

logger = logging.getLogger(__name__)

# (user_id, idioma) de quem disparou o update atual.
_current: contextvars.ContextVar[tuple[int, str] | None] = contextvars.ContextVar(
    "current_user_lang", default=None
)


class UserLanguageMiddleware(BaseMiddleware):
    """Guarda o idioma de quem mandou o update, para as respostas saírem nele."""

    def __init__(self, db) -> None:
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        token = None
        if user is not None:
            try:
                lang = await user_lang(self.db, user)
            except Exception:  # noqa: BLE001
                lang = DEFAULT_LANG
            token = _current.set((user.id, lang))
        try:
            return await handler(event, data)
        finally:
            if token is not None:
                _current.reset(token)


class OutgoingTextMiddleware(BaseRequestMiddleware):
    """Remove emojis e traduz textos, legendas e botões antes de enviar."""

    def __init__(self, db) -> None:
        self.db = db

    async def _lang_for(self, method) -> str:
        chat_id = getattr(method, "chat_id", None)
        current = _current.get()
        if chat_id is None:
            # Ex.: answer_callback_query — vai para quem clicou.
            return current[1] if current else DEFAULT_LANG
        if not isinstance(chat_id, int) or chat_id < 0:
            return DEFAULT_LANG  # grupos ficam em português
        if current and current[0] == chat_id:
            return current[1]
        try:
            saved = await self.db.get_user_language(chat_id)
        except Exception:  # noqa: BLE001
            saved = None
        return saved if saved in LANGUAGES else DEFAULT_LANG

    @staticmethod
    def _markup(markup, lang: str):
        if isinstance(markup, InlineKeyboardMarkup):
            rows = [
                [b.model_copy(update={"text": render(b.text, lang)}) for b in row]
                for row in markup.inline_keyboard
            ]
            return markup.model_copy(update={"inline_keyboard": rows})
        if isinstance(markup, ReplyKeyboardMarkup):
            rows = [
                [b.model_copy(update={"text": render(b.text, lang)}) for b in row]
                for row in markup.keyboard
            ]
            return markup.model_copy(update={"keyboard": rows})
        if isinstance(markup, ForceReply) and markup.input_field_placeholder:
            return markup.model_copy(
                update={"input_field_placeholder": render(markup.input_field_placeholder, lang)}
            )
        return markup

    async def __call__(self, make_request, bot, method):
        try:
            lang = await self._lang_for(method)
            updates: dict[str, Any] = {}
            if isinstance(getattr(method, "text", None), str) and not getattr(method, "entities", None):
                updates["text"] = render(method.text, lang)
            if isinstance(getattr(method, "caption", None), str) and not getattr(method, "caption_entities", None):
                updates["caption"] = render(method.caption, lang)
            markup = getattr(method, "reply_markup", None)
            if markup is not None:
                updates["reply_markup"] = self._markup(markup, lang)
            for key, value in updates.items():
                setattr(method, key, value)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao filtrar texto de saída; enviando original")
        return await make_request(bot, method)


def set_current_language(user_id: int, lang: str) -> None:
    """Usado logo após o usuário trocar de idioma, no mesmo clique."""
    _current.set((user_id, lang))
