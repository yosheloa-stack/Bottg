"""Helpers de interface: banner animado do menu e edição de telas.

Mantém o banner (GIF/imagem) fixo no topo do menu e navega entre telas
editando a legenda, sem reenviar a mídia a cada clique. O file_id é
cacheado em memória após o primeiro envio para não subir o arquivo toda vez.
"""
from __future__ import annotations

import logging
import os

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
)

logger = logging.getLogger(__name__)

# Cache do file_id do banner (evita reupload a cada /start)
_banner_file_id: str | None = None


def _banner_input(path: str):
    if path.startswith("http://") or path.startswith("https://"):
        return URLInputFile(path)
    return FSInputFile(path)


async def send_menu(
    message: Message, banner: str, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Envia o menu como animação (GIF) com legenda + botões. Faz fallback para texto."""
    global _banner_file_id

    is_url = banner.startswith("http")
    available = bool(_banner_file_id) or is_url or os.path.exists(banner)
    if not available:
        # Sem banner disponível: envia só texto
        await message.answer(text, reply_markup=reply_markup)
        return

    try:
        source = _banner_file_id or _banner_input(banner)
        sent = await message.answer_animation(
            animation=source, caption=text, reply_markup=reply_markup
        )
        if sent.animation:
            _banner_file_id = sent.animation.file_id
        elif sent.document:
            _banner_file_id = sent.document.file_id
    except TelegramBadRequest as exc:
        logger.warning("Falha ao enviar banner animado (%s); enviando texto", exc)
        await message.answer(text, reply_markup=reply_markup)


async def edit_screen(
    query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Edita a tela atual mantendo a mídia quando houver (edita legenda), senão o texto."""
    msg = query.message
    try:
        if msg.animation or msg.photo or msg.document or msg.video:
            await msg.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        # Fallback: apaga e reenvia como texto
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        await msg.answer(text, reply_markup=reply_markup)
