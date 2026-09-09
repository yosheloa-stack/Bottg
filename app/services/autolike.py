"""Cliente da Auto Like System API (likes, info, skin, auto-like).

Documentação: https://autolikesystem.com.br/docs
Base URL padrão: https://autolikesystem.com.br
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=35)


@dataclass
class ApiResult:
    ok: bool
    status: int
    data: dict[str, Any]
    error: str | None = None


class AutoLikeApi:
    def __init__(self, base_url: str, api_key: str, default_region: str = "BR") -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._region = default_region

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
            logger.warning("Erro de rede na AutoLike API %s: %s", path, exc)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na AutoLike API %s", path)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))

    async def info_player(self, game_id: str, region: str | None = None) -> ApiResult:
        return await self._get(
            "/info-player", {"id": game_id, "region": region or self._region}
        )

    async def get_skin(self, game_id: str, region: str | None = None) -> ApiResult:
        return await self._get(
            "/get-skin", {"id": game_id, "region": region or self._region}
        )

    async def send_like(self, game_id: str, region: str | None = None) -> ApiResult:
        # A sub-API atual é /v1/like. O servidor decide internamente entre
        # GGx e o motor próprio; o bot não expõe esse caminho ao usuário.
        return await self._get(
            "/v1/like", {"uid": game_id, "region": region or self._region, "qtd": 220}
        )

    async def like_status(self, game_id: str, region: str | None = None) -> ApiResult:
        return await self._get(
            "/like-status", {"id": game_id, "region": region or self._region}
        )

    async def add_auto(
        self, game_id: str, days: int = 30, region: str | None = None
    ) -> ApiResult:
        return await self._get(
            "/add-auto",
            {"id": game_id, "dias": days, "region": region or self._region},
        )
