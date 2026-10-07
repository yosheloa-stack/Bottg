"""Cliente exclusivo da Likes Painel API.

Endpoints:
- GET /api/like
- GET /api/quota

Autenticação:
- Header X-API-Key
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=35)


@dataclass
class LikesApiResult:
    ok: bool
    status: int
    data: dict[str, Any]
    error: str | None = None


class LikesApi:
    def __init__(self, base_url: str, api_key: str, quantity: int = 100) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._quantity = max(1, min(int(quantity), 200))

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> LikesApiResult:
        url = f"{self._base}{path}"
        headers = {"X-API-Key": self._key}

        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(url, params=params or {}, headers=headers) as resp:
                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        raw = await resp.text()
                        return LikesApiResult(
                            ok=False,
                            status=resp.status,
                            data={"raw": raw},
                            error="Resposta inválida da API de likes.",
                        )

                    if not isinstance(payload, dict):
                        return LikesApiResult(
                            ok=False,
                            status=resp.status,
                            data={},
                            error="Resposta inválida da API de likes.",
                        )

                    api_data = payload.get("data")
                    if not isinstance(api_data, dict):
                        api_data = {}

                    error = payload.get("error")
                    return LikesApiResult(
                        ok=resp.status == 200 and payload.get("success") is True,
                        status=resp.status,
                        data=api_data,
                        error=str(error) if error else None,
                    )
        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede na Likes Painel API %s: %s", path, exc)
            return LikesApiResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na Likes Painel API %s", path)
            return LikesApiResult(ok=False, status=0, data={}, error=str(exc))

    async def send_like(
        self, uid: str, quantity: int | None = None
    ) -> LikesApiResult:
        amount = self._quantity if quantity is None else int(quantity)
        amount = max(1, min(amount, 200))
        return await self._get(
            "/api/like",
            {"uid": uid, "quantity": amount},
        )

    async def quota(self) -> LikesApiResult:
        return await self._get("/api/quota")
