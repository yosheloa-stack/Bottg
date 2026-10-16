"""Cliente da FFHub Shop para produtos pagos.

Base: https://ffhub-shop.shardweb.app
Autenticação: X-API-Key
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(
    total=120,
    connect=15,
    sock_connect=15,
    sock_read=105,
)


@dataclass
class FFHubResult:
    ok: bool
    status: int
    data: dict[str, Any]
    error: str | None = None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _find_value(data: Any, keys: tuple[str, ...]) -> Any:
    """Procura uma chave também dentro de objetos/listas aninhados."""
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        for value in data.values():
            found = _find_value(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_value(item, keys)
            if found is not None:
                return found
    return None


def parse_like_delivery(data: dict[str, Any]) -> dict[str, Any]:
    """Normaliza o retorno da rota /api/buy/likes.

    Não usa o campo genérico 'likes' como quantidade enviada porque algumas
    respostas da FFHub usam esse nome para outro contador.
    """
    before_raw = _find_value(
        data,
        (
            "likes_antes",
            "likes_before",
            "before_likes",
            "old_likes",
            "previous_likes",
            "likesBefore",
        ),
    )
    after_raw = _find_value(
        data,
        (
            "likes_depois",
            "likes_after",
            "after_likes",
            "new_likes",
            "current_likes",
            "likesAfter",
        ),
    )
    sent_raw = _find_value(
        data,
        (
            "likes_enviados",
            "likes_sent",
            "sent_likes",
            "likes_added",
            "added_likes",
            "likes_adicionados",
            "curtidas_enviadas",
            "quantidade_enviada",
            "quantity_sent",
            "delivered_likes",
            "enviados",
        ),
    )

    before = _to_int(before_raw)
    after = _to_int(after_raw)
    sent = _to_int(sent_raw)

    # Quando a API fornece os totais antes/depois, a diferença é a fonte
    # mais confiável para o que realmente entrou na conta.
    if before is not None and after is not None and after >= before:
        delta = after - before
        if delta > 0:
            sent = delta

    nick = _find_value(
        data,
        ("nickname", "nick", "player_name", "playerName", "nome", "name"),
    )

    return {
        "sent": sent,
        "before": before if before is not None else before_raw,
        "after": after if after is not None else after_raw,
        "nickname": nick or "Jogador",
    }


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
                    raw = await resp.text()

                    logger.info(
                        "FFHub resposta | path=%s status=%s bytes=%s",
                        path,
                        resp.status,
                        len(raw),
                    )

                    try:
                        parsed = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        parsed = {"raw": raw}

                    if isinstance(parsed, dict):
                        data = parsed
                    else:
                        data = {"result": parsed}

                    # A documentação não define um campo único de sucesso para
                    # todas as rotas. Respeita sucesso/success quando vier;
                    # caso contrário, usa o status HTTP 2xx.
                    explicit = data.get("sucesso")
                    if explicit is None:
                        explicit = data.get("success")

                    ok = 200 <= resp.status < 300
                    if explicit is not None:
                        if isinstance(explicit, str):
                            explicit_ok = explicit.strip().lower() in {
                                "true", "1", "ok", "success", "sucesso"
                            }
                        else:
                            explicit_ok = bool(explicit)
                        ok = ok and explicit_ok

                    error = (
                        data.get("erro")
                        or data.get("error")
                        or data.get("message")
                        or data.get("mensagem")
                    )

                    if not ok:
                        logger.warning(
                            "FFHub Shop falhou path=%s status=%s body=%s",
                            path,
                            resp.status,
                            raw[:1500],
                        )

                    return FFHubResult(
                        ok=ok,
                        status=resp.status,
                        data=data,
                        error=str(error) if error else None,
                    )

        except asyncio.TimeoutError:
            logger.warning("Timeout FFHub Shop %s após 120s", path)
            return FFHubResult(
                ok=False,
                status=0,
                data={},
                error="A FFHub não respondeu em até 120 segundos.",
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
