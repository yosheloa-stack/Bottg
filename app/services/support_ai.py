"""Cliente do AI Gateway da Square Cloud (compatível com OpenAI).

Documentação: https://docs.squarecloud.app/en/api-reference/ai-gateway
- POST https://api.squarecloud.app/v2/ai/chat/completions
- Header Authorization: chave da conta Square Cloud com escopo ai:chat
- Sem streaming; suporta tool calling no formato OpenAI.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.squarecloud.app/v2/ai"
MODEL = "cubic"
TIMEOUT = aiohttp.ClientTimeout(total=90, connect=15)
# Plano Standard: 1 requisição por vez e 5s entre requisições.
MIN_INTERVAL_SECONDS = 5.0
MAX_RETRY_WAIT_SECONDS = 20.0


@dataclass
class AiResult:
    ok: bool
    message: dict[str, Any]
    error: str = ""


class SquareCloudAI:
    def __init__(self, api_key: str, base_url: str = BASE_URL) -> None:
        self._key = (api_key or "").strip()
        self._base = base_url.rstrip("/")
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._key)

    async def _post(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any], str | None]:
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{self._base}/chat/completions", json=payload, headers=headers
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001
                    data = {"raw": (await resp.text())[:300]}
                return resp.status, data if isinstance(data, dict) else {}, resp.headers.get(
                    "Retry-After"
                )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1200,
    ) -> AiResult:
        if not self.configured:
            return AiResult(False, {}, "IA não configurada.")

        payload: dict[str, Any] = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_completion_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with self._lock:
            for attempt in range(2):
                wait = MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_call)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    status, data, retry_after = await self._post(payload)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Falha de rede no AI Gateway")
                    return AiResult(False, {}, f"Falha de conexão com a IA: {exc}")
                finally:
                    self._last_call = time.monotonic()

                if status == 200:
                    choices = data.get("choices") or []
                    message = (choices[0].get("message") if choices else None) or {}
                    return AiResult(True, message)

                error = data.get("error") if isinstance(data.get("error"), dict) else {}
                code = str(error.get("code") or "")
                logger.warning(
                    "AI Gateway erro | status=%s code=%s msg=%s",
                    status,
                    code,
                    error.get("message") or data,
                )
                retryable = status in (429, 503) and code not in {
                    "daily_limit_exceeded",
                    "weekly_limit_exceeded",
                    "daily_capacity_reached",
                }
                if attempt == 0 and retryable:
                    try:
                        delay = float(retry_after or MIN_INTERVAL_SECONDS)
                    except ValueError:
                        delay = MIN_INTERVAL_SECONDS
                    if delay <= MAX_RETRY_WAIT_SECONDS:
                        await asyncio.sleep(delay)
                        continue
                return AiResult(False, {}, code or f"HTTP {status}")
        return AiResult(False, {}, "Sem resposta da IA.")
