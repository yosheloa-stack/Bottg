"""Funções utilitárias."""
from __future__ import annotations

import re

_ID_RE = re.compile(r"^\d{5,20}$")


def is_valid_game_id(text: str) -> bool:
    return bool(_ID_RE.match(text.strip()))


def clean_game_id(text: str) -> str:
    return text.strip()


def format_price(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def extract_nick(data: dict) -> str | None:
    """Tenta extrair o nickname do jogador de uma resposta variada da API."""
    if not isinstance(data, dict):
        return None
    # Caminhos comuns em respostas de APIs de Free Fire
    for key in ("nickname", "nick", "nome", "name", "username"):
        if data.get(key):
            return str(data[key])
    player = data.get("player") or data.get("jogador") or data.get("basicInfo")
    if isinstance(player, dict):
        for key in ("nickname", "nick", "nome", "name", "username"):
            if player.get(key):
                return str(player[key])
    return None
