"""Clientes das APIs de Free Fire usadas pelo bot.

A API antiga continua responsável por consulta de jogador, skin e Auto-Like.
O envio avulso de likes usa a Likes Painel API:
    GET /api/like
    Header: X-API-Key
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
    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_region: str = "BR",
        *,
        likes_base_url: str = "http://likespainel.squareweb.app",
        likes_api_key: str | None = None,
        likes_quantity: int = 100,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._region = default_region

        self._likes_base = likes_base_url.rstrip("/")
        self._likes_key = likes_api_key or api_key
        self._likes_quantity = max(1, min(int(likes_quantity), 200))

    async def _get_legacy(self, path: str, params: dict[str, Any]) -> ApiResult:
        """Requisição para a API antiga (info, skin e Auto-Like)."""
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
            logger.warning("Erro de rede na API antiga %s: %s", path, exc)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na API antiga %s", path)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))

    async def _get_likes(
        self, path: str, params: dict[str, Any] | None = None
    ) -> ApiResult:
        """Requisição autenticada para a Likes Painel API."""
        url = f"{self._likes_base}{path}"
        headers = {"X-API-Key": self._likes_key}

        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(url, params=params or {}, headers=headers) as resp:
                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        raw = await resp.text()
                        return ApiResult(
                            ok=False,
                            status=resp.status,
                            data={"raw": raw},
                            error="Resposta inválida da API de likes.",
                        )

                    if not isinstance(payload, dict):
                        return ApiResult(
                            ok=False,
                            status=resp.status,
                            data={},
                            error="Resposta inválida da API de likes.",
                        )

                    success = payload.get("success") is True
                    error = payload.get("error")
                    api_data = payload.get("data")

                    if not isinstance(api_data, dict):
                        api_data = {}

                    return ApiResult(
                        ok=resp.status == 200 and success,
                        status=resp.status,
                        data=api_data,
                        error=str(error) if error else None,
                    )
        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede na Likes Painel API %s: %s", path, exc)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na Likes Painel API %s", path)
            return ApiResult(ok=False, status=0, data={}, error=str(exc))

    async def info_player(self, game_id: str, region: str | None = None) -> ApiResult:
        return await self._get_legacy(
            "/info-player", {"id": game_id, "region": region or self._region}
        )

    async def get_skin(self, game_id: str, region: str | None = None) -> ApiResult:
        return await self._get_legacy(
            "/get-skin", {"id": game_id, "region": region or self._region}
        )

    async def send_like(
        self,
        game_id: str,
        region: str | None = None,
        quantity: int | None = None,
    ) -> ApiResult:
        # A nova API atende somente o servidor Brasil e não usa o parâmetro region.
        del region
        amount = self._likes_quantity if quantity is None else int(quantity)
        amount = max(1, min(amount, 200))
        return await self._get_likes(
            "/api/like",
            {"uid": game_id, "quantity": amount},
        )

    async def quota(self) -> ApiResult:
        """Consulta quota da Key sem consumir likes."""
        return await self._get_likes("/api/quota")

    async def like_status(self, game_id: str, region: str | None = None) -> ApiResult:
        return await self._get_legacy(
            "/like-status", {"id": game_id, "region": region or self._region}
        )

    async def add_auto(
        self, game_id: str, days: int = 30, region: str | None = None
    ) -> ApiResult:
        return await self._get_legacy(
            "/add-auto",
            {"id": game_id, "dias": days, "region": region or self._region},
        )
