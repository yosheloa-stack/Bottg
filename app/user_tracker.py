"""Salva o @username e o nome de cada cliente que fala com o bot."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.database import Database

logger = logging.getLogger(__name__)


class UserTrackerMiddleware(BaseMiddleware):
    """Grava no banco só quando o @ ou o nome do usuário mudam."""

    def __init__(self) -> None:
        self._seen: dict[int, tuple[str | None, str | None]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        if user is not None and not user.is_bot:
            info = (user.username, user.first_name)
            if self._seen.get(user.id) != info:
                db: Database | None = data.get("db")
                if db is not None:
                    try:
                        await db.track_user(user.id, user.username, user.first_name)
                        self._seen[user.id] = info
                    except Exception:  # noqa: BLE001
                        logger.exception("Falha ao salvar usuário %s", user.id)
        return await handler(event, data)
