"""Cliente exclusivo da Likes Painel API.

Contrato da API:
- GET /api/like?uid=<UID>&quantity=<1-200>
- GET /api/quota
- Header obrigatório: X-API-Key
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# A rota /api/like pode levar bem mais de 35s para confirmar o contador.
# Mantemos uma janela maior para não transformar processamento lento em falso erro.
TIMEOUT = aiohttp.ClientTimeout(
    total=110,
    connect=15,
    sock_connect=15,
    sock_read=100,
)


@dataclass
class LikesApiResult:
    ok: bool
    status: int
    data: dict[str, Any]
    error: str | None = None


def _counter_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip().replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


class LikesApi:
    def __init__(self, base_url: str, api_key: str, quantity: int = 200) -> None:
        base = base_url.rstrip("/")
        if base == "http://likespainel.squareweb.app":
            base = "https://likespainel.squareweb.app"
        self._base = base
        self._key = api_key.strip()
        # A Likes Painel aceita no máximo 200 por chamada.
        self._quantity = max(1, min(int(quantity), 200))

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> LikesApiResult:
        url = f"{self._base}{path}"
        headers = {
            "X-API-Key": self._key,
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(url, params=params or {}, headers=headers) as resp:
                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        raw = await resp.text()
                        logger.warning(
                            "Likes API retornou resposta não-JSON | status=%s | body=%r",
                            resp.status,
                            raw[:300],
                        )
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

        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning("Timeout na Likes Painel API %s: %s", path, exc)
            return LikesApiResult(
                ok=False,
                status=0,
                data={
                    "timeout": True,
                    "delivery_unknown": path == "/api/like",
                },
                error=(
                    "A API demorou para confirmar o resultado."
                    if path == "/api/like"
                    else "A API demorou para responder."
                ),
            )
        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede na Likes Painel API %s: %s", path, exc)
            return LikesApiResult(ok=False, status=0, data={}, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na Likes Painel API %s", path)
            return LikesApiResult(ok=False, status=0, data={}, error=str(exc))

    async def quota(self) -> LikesApiResult:
        """Consulta quota da Key. Não consome likes."""
        return await self._get("/api/quota")

    async def send_like(
        self, uid: str, quantity: int | None = None
    ) -> LikesApiResult:
        """Envia até 200 e normaliza a quantidade REAL pelo contador."""
        requested = self._quantity if quantity is None else int(quantity)
        requested = max(1, min(requested, 200))

        quota = await self.quota()
        if not quota.ok:
            # Mantém exatamente o status/erro retornado pela API.
            return quota

        remaining = quota.data.get("remaining")
        per_request_limit = quota.data.get("per_request_limit")

        try:
            remaining_int = int(remaining)
        except (TypeError, ValueError):
            remaining_int = requested

        try:
            per_request_int = int(per_request_limit)
        except (TypeError, ValueError):
            per_request_int = 200

        if remaining_int <= 0:
            return LikesApiResult(
                ok=False,
                status=429,
                data=quota.data,
                error="Limite diário atingido.",
            )

        # O contrato público aceita até 200. Não reduzimos silenciosamente o
        # pedido para um per_request_limit antigo retornado pelo /api/quota.
        # Se a key realmente não aceitar 200, a /api/like devolve o erro real.
        amount = min(requested, remaining_int, 200)
        if amount <= 0:
            return LikesApiResult(
                ok=False,
                status=429,
                data=quota.data,
                error="Quota diária insuficiente para a quantidade solicitada.",
            )

        logger.info(
            "Envio Likes API | uid=%s | solicitado=%s | enviado_para_api=%s | "
            "quota_restante=%s | limite_req=%s",
            uid,
            requested,
            amount,
            remaining_int,
            per_request_int,
        )

        result = await self._get(
            "/api/like",
            {"uid": uid, "quantity": amount},
        )
        if isinstance(result.data, dict):
            result.data.setdefault("region", "BR")
            result.data.setdefault("requested", amount)

            before = _counter_int(result.data.get("likes_before"))
            after = _counter_int(result.data.get("likes_after"))
            confirmed = _counter_int(result.data.get("likes_confirmed"))
            reported = _counter_int(result.data.get("likes_sent"))

            # Fonte da verdade: contador antes/depois.
            if before is not None and after is not None and after >= before:
                real_sent = after - before
            elif confirmed is not None:
                real_sent = max(0, confirmed)
            elif reported is not None:
                real_sent = max(0, reported)
            else:
                real_sent = 0

            result.data["likes_sent"] = real_sent
            result.data["likes_confirmed"] = real_sent

            logger.info(
                "Likes API confirmado | uid=%s antes=%s depois=%s real=%s reportado=%s",
                uid,
                before,
                after,
                real_sent,
                reported,
            )
        return result
