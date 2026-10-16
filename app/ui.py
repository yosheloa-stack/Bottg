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

# Cache do file_id do banner padrão (evita reupload a cada /start)
_banner_file_id: str | None = None
_banner_cache_source: str | None = None


def _banner_input(path: str):
    if path.startswith("http://") or path.startswith("https://"):
        return URLInputFile(path)
    return FSInputFile(path)


async def send_menu(
    message: Message, banner: str, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Envia foto/GIF/vídeo configurado pelo dono ou o banner padrão."""
    global _banner_file_id, _banner_cache_source

    # Mídia Telegram salva pelo /setmenu.
    if banner.startswith("tg:"):
        try:
            _, media_type, file_id = banner.split(":", 2)
            if media_type == "photo":
                await message.answer_photo(
                    photo=file_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            elif media_type == "video":
                await message.answer_video(
                    video=file_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                await message.answer_animation(
                    animation=file_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Falha ao usar mídia personalizada do menu (%s); usando padrão",
                exc,
            )

    is_url = banner.startswith("http")
    if _banner_cache_source != banner:
        _banner_file_id = None
        _banner_cache_source = banner

    available = bool(_banner_file_id) or is_url or os.path.exists(banner)
    if not available:
        await message.answer(text, reply_markup=reply_markup)
        return

    try:
        source = _banner_file_id or _banner_input(banner)
        sent = await message.answer_animation(
            animation=source,
            caption=text,
            reply_markup=reply_markup,
        )
        if sent.animation:
            _banner_file_id = sent.animation.file_id
        elif sent.document:
            _banner_file_id = sent.document.file_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao enviar banner/menu (%s); enviando texto", exc)
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
        logger.warning("Falha ao editar tela do Telegram: %s", exc)
        try:
            await msg.answer(text, reply_markup=reply_markup)
        except Exception:  # noqa: BLE001
            logger.exception("Falha no fallback de navegação do menu")
            raise
    except Exception as exc:  # noqa: BLE001
        # Alguns callbacks antigos podem apontar para mensagens/mídias que
        # já não aceitam edição. Não deixa o clique travado por causa disso.
        logger.exception("Erro inesperado ao editar tela do menu: %s", exc)
        try:
            await msg.answer(text, reply_markup=reply_markup)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao reenviar tela após erro de edição")
            raise
