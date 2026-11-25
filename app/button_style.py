"""Padrão visual dos botões: todos verdes e sem emoji.

Em vez de editar cada teclado do bot, este middleware ajusta qualquer
teclado inline logo antes de ele ir para o Telegram:

- remove os emojis do texto do botão ("💰 Adicionar saldo" -> "Adicionar saldo");
- aplica a cor verde (style="success", Bot API 9.4+) em todo botão que
  ainda não tenha uma cor definida.

Assim botões novos, criados em qualquer parte do código, já saem no padrão.
Em apps do Telegram antigos a cor é ignorada e o botão aparece normal.
"""
from __future__ import annotations

import re

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

COR_PADRAO = ButtonStyle.SUCCESS  # verde

_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emojis, símbolos e pictogramas
    "\U0001F1E6-\U0001F1FF"  # bandeiras
    "←-⇿"          # setas
    "⌀-⏿"          # símbolos técnicos (⏳ ⌛ ⏰)
    "☀-➿"          # símbolos diversos e dingbats (✅ ✏️ ❤️ ⚙️)
    "⬀-⯿"          # setas e estrelas (⬅️ ⭐)
    "⤴⤵〰〽㊗㊙"
    "︎️‍⃣"  # variação, junção e keycap
    "]+"
)


def sem_emoji(texto: str) -> str:
    limpo = re.sub(r"\s{2,}", " ", _EMOJI.sub("", texto or "")).strip(" -•|")
    return limpo or texto


def estilizar(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    linhas: list[list[InlineKeyboardButton]] = []
    for linha in markup.inline_keyboard:
        nova = []
        for botao in linha:
            nova.append(
                botao.model_copy(
                    update={
                        "text": sem_emoji(botao.text),
                        "style": botao.style or COR_PADRAO,
                    }
                )
            )
        linhas.append(nova)
    return InlineKeyboardMarkup(inline_keyboard=linhas)


class BotoesVerdes(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            method.reply_markup = estilizar(markup)
        return await make_request(bot, method)
