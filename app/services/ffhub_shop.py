"""Cliente da FFHub Shop para produtos pagos.

Base: https://ffhub-shop.shardweb.app
Autenticação: X-API-Key
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=45)


@dataclass
class FFHubResult:
    ok: bool
    status: int
    data: dict[str, Any]
    error: str | None = None


class FFHubShopApi:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key.strip()

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> FFHubResult:
        if not self.configured:
            return FFHubResult(
                ok=False,
                status=0,
                data={},
                error="FFHub Shop não configurada.",
            )

        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    json=payload if method != "GET" else None,
                ) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        raw = await resp.text()
                        data = {"raw": raw}

                    if not isinstance(data, dict):
                        data = {}

                    # A documentação não define um campo único de sucesso para
                    # todas as rotas. Respeita sucesso/success quando vier;
                    # caso contrário, usa o status HTTP 2xx.
                    explicit = data.get("sucesso")
                    if explicit is None:
                        explicit = data.get("success")

                    ok = 200 <= resp.status < 300
                    if explicit is not None:
                        ok = ok and bool(explicit)

                    error = (
                        data.get("erro")
                        or data.get("error")
                        or data.get("message")
                        or data.get("mensagem")
                    )

                    if not ok:
                        logger.warning(
                            "FFHub Shop falhou path=%s status=%s data=%s",
                            path,
                            resp.status,
                            data,
                        )

                    return FFHubResult(
                        ok=ok,
                        status=resp.status,
                        data=data,
                        error=str(error) if error else None,
                    )

        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede FFHub Shop %s: %s", path, exc)
            return FFHubResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado FFHub Shop %s", path)
            return FFHubResult(ok=False, status=0, data={}, error=str(exc))

    async def send_passe(self, uid: str) -> FFHubResult:
        return await self._request(
            "POST",
            "/api/buy/gift",
            {"gift_id": "0", "target_uid": uid},
        )

    async def send_team_shirt(self, uid: str) -> FFHubResult:
        return await self._request(
            "POST",
            "/api/buy/gift",
            {"gift_id": "times", "target_uid": uid},
        )

    async def buy_diamonds(self, token: str, quantity: str | int) -> FFHubResult:
        return await self._request(
            "POST",
            "/api/buy/diamonds",
            {"token": token, "quantidade": str(quantity)},
        )

    async def send_paid_likes(self, uid: str) -> FFHubResult:
        return await self._request(
            "POST",
            "/api/buy/likes",
            {"target_id": uid},
        )

    async def redeem_snickers(self, token: str) -> FFHubResult:
        return await self._request(
            "POST",
            "/api/buy/snickers",
            {"token": token},
        )

    async def balance(self) -> FFHubResult:
        return await self._request("GET", "/api/balance")
