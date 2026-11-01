"""Leitura tolerante de respostas HTTP (aiohttp).

Alguns provedores fecham a conexão antes de terminar o corpo chunked, e o
aiohttp levanta ``ClientPayloadError`` ("Response payload is not completed").
Muitas vezes o JSON já chegou inteiro e só faltou o terminador; aqui lemos o
corpo em pedaços, guardamos o que chegou e tentamos aproveitar.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


async def read_body_tolerant(resp: aiohttp.ClientResponse) -> tuple[bytes, bool]:
    """Lê o corpo inteiro. Devolve (bytes, truncado)."""
    chunks: list[bytes] = []
    truncated = False
    try:
        async for chunk in resp.content.iter_chunked(8192):
            chunks.append(chunk)
    except aiohttp.ClientPayloadError as exc:
        truncated = True
        logger.warning(
            "Resposta incompleta de %s (status=%s, %s bytes recebidos): %s",
            resp.url.host,
            resp.status,
            sum(len(c) for c in chunks),
            exc,
        )
    return b"".join(chunks), truncated


def parse_json_lenient(raw: bytes) -> Any:
    """Tenta decodificar JSON, mesmo com lixo/corte depois do objeto."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError:
        return None


async def read_json_tolerant(
    resp: aiohttp.ClientResponse,
) -> tuple[Any, str, bool]:
    """Devolve (json ou None, texto bruto, truncado)."""
    raw, truncated = await read_body_tolerant(resp)
    return parse_json_lenient(raw), raw.decode("utf-8", errors="replace"), truncated
