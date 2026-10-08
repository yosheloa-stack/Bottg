"""Cliente de fallback para a API Auto System.

Base: https://autolikesystem.com.br
Endpoint de envio único: GET /v1/like
Autenticação: X-Api-Key
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.services.likes import LikesApiResult

logger = logging.getLogger(__name__)

# A documentação informa que /v1/like pode levar até 80 segundos.
TIMEOUT = aiohttp.ClientTimeout(total=95)


class AutoSystemLikesApi:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        quantity: int = 220,
        region: str = "BR",
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key.strip()
        self._quantity = max(1, min(int(quantity), 2000))
        self._region = region or "BR"

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def send_like(
        self, uid: str, quantity: int | None = None
    ) -> LikesApiResult:
        if not self.enabled:
            return LikesApiResult(
                ok=False,
                status=0,
                data={},
                error="API Auto System de fallback não configurada.",
            )

        amount = self._quantity if quantity is None else int(quantity)
        amount = max(1, min(amount, 2000))
        url = f"{self._base}/v1/like"
        headers = {
            "X-Api-Key": self._key,
            "Accept": "application/json",
        }
        params = {
            "uid": uid,
            "region": self._region,
            "qtd": amount,
        }

        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        raw = await resp.text()
                        logger.warning(
                            "Auto System retornou resposta não-JSON | status=%s | body=%r",
                            resp.status,
                            raw[:300],
                        )
                        return LikesApiResult(
                            ok=False,
                            status=resp.status,
                            data={"raw": raw},
                            error="Resposta inválida da API Auto System.",
                        )

                    if not isinstance(payload, dict):
                        return LikesApiResult(
                            ok=False,
                            status=resp.status,
                            data={},
                            error="Resposta inválida da API Auto System.",
                        )

                    sucesso = payload.get("sucesso") is True
                    status_text = str(payload.get("status") or "")
                    codigo = str(payload.get("codigo") or "")
                    erro = payload.get("erro") or payload.get("mensagem")

                    normalized: dict[str, Any] = {
                        "uid": str(payload.get("uid") or uid),
                        "nickname": payload.get("nick") or payload.get("nickname"),
                        "likes_before": payload.get("likes_antes"),
                        "likes_after": payload.get("likes_depois"),
                        "likes_sent": payload.get("likes_enviados", 0),
                        "likes_confirmed": payload.get("likes_confirmados"),
                        "notice": payload.get("aviso"),
                        "status": status_text or ("success" if sucesso else ""),
                        "code": codigo,
                        "libera_em": payload.get("libera_em"),
                        "libera_em_segundos": payload.get("libera_em_segundos"),
                        "tempo_restante": payload.get("tempo_restante"),
                        "provider": "autosystem",
                        "region": self._region,
                        "target": amount,
                    }

                    # A API usa HTTP 200 também para cooldown e estados operacionais.
                    if sucesso and status_text == "aguardando":
                        return LikesApiResult(
                            ok=False,
                            status=409,
                            data=normalized,
                            error=str(
                                payload.get("mensagem")
                                or "Esse ID ainda está no período de espera."
                            ),
                        )

                    if resp.status == 200 and sucesso:
                        return LikesApiResult(
                            ok=True,
                            status=200,
                            data=normalized,
                            error=None,
                        )

                    # sucesso=false com HTTP 200 = estado operacional sem entrega.
                    if resp.status == 200 and not sucesso:
                        return LikesApiResult(
                            ok=False,
                            status=503,
                            data=normalized,
                            error=str(erro or "A Auto System não entregou likes agora."),
                        )

                    return LikesApiResult(
                        ok=False,
                        status=resp.status,
                        data=normalized,
                        error=str(erro or f"Auto System retornou HTTP {resp.status}."),
                    )

        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede na Auto System /v1/like: %s", exc)
            return LikesApiResult(ok=False, status=0, data={}, error=str(exc))
        except TimeoutError:
            logger.warning("Timeout na Auto System /v1/like para uid=%s", uid)
            return LikesApiResult(
                ok=False,
                status=0,
                data={},
                error=(
                    "A Auto System demorou além do esperado. "
                    "Confira o perfil antes de tentar novamente."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na Auto System /v1/like")
            return LikesApiResult(ok=False, status=0, data={}, error=str(exc))
