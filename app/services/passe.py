"""Cliente da API de Passe Booyah.

Documentação: https://autolikesystem.com.br/passe/docs
Base URL: https://fluxggx.squareweb.app (key separada da API principal)
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.services.autolike import ApiResult

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=35)


class PasseApi:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key

    async def _get(self, path: str, params: dict[str, Any]) -> ApiResult:
        url = f"{self._base}{path}"
        params = {"key": self._key, **params}
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(url, params=params) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text()
                        data = {"raw": text}
                    ok = resp.status == 200 and bool(
                        data.get("sucesso", True) if isinstance(data, dict) else True
                    )
                    return ApiResult(ok=ok, status=resp.status, data=data or {})
        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede na Passe API %s: %s", path, exc)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na Passe API %s", path)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))

    async def send_passe(self, game_id: str) -> ApiResult:
        return await self._get("/send-passe", {"id": game_id})

    async def estoque(self) -> ApiResult:
        return await self._get("/passe/estoque", {})
