"""Cliente da API de produtos pagos (presentes, diamantes, likes, Snickers).

A URL e a chave vêm do ambiente (FFHUB_BASE_URL / FFHUB_API_KEY) ou do
/setkey ffhub. Nenhuma mensagem deste módulo deve chegar crua ao cliente:
use ``public_error`` para converter falhas em mensagens formais e genéricas.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from app.services.http_utils import parse_json_lenient, read_body_tolerant

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(
    total=120,
    connect=15,
    sock_connect=15,
    sock_read=105,
)


CATALOG_TTL = 300  # segundos


@dataclass
class GiftItem:
    gift_id: str
    category: str
    name: str
    cost: Decimal
    disabled: bool = False
    stock: int | None = None  # None = não informado

    @property
    def available(self) -> bool:
        return not self.disabled and self.cost > 0 and (
            self.stock is None or self.stock > 0
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
        self._catalog_cache: list[GiftItem] | None = None
        self._catalog_at = 0.0

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
        *,
        auth: bool = True,
        params: dict[str, str] | None = None,
    ) -> FFHubResult:
        if auth and not self.configured:
            return FFHubResult(
                ok=False,
                status=0,
                data={},
                error="not_configured",
            )

        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.request(
                    method,
                    f"{self._base}{path}",
                    headers=(
                        self._headers()
                        if auth
                        else {"Accept": "application/json"}
                    ),
                    params=params,
                    json=payload if method != "GET" else None,
                ) as resp:
                    raw_bytes, truncated = await read_body_tolerant(resp)
                    raw = raw_bytes.decode("utf-8", errors="replace")

                    logger.info(
                        "Produtos API resposta | path=%s status=%s bytes=%s truncado=%s",
                        path,
                        resp.status,
                        len(raw),
                        truncated,
                    )

                    parsed = parse_json_lenient(raw_bytes) if raw else {}
                    if truncated and not isinstance(parsed, dict):
                        # Corpo cortado e ilegível: não dá para saber se entregou.
                        return FFHubResult(
                            ok=False,
                            status=0,
                            data={"raw": raw[:1500], "truncated": True, "delivery_unknown": True},
                            error="incomplete",
                        )
                    if parsed is None:
                        parsed = {"raw": raw[:1500]}

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
                            "Produtos API falhou path=%s status=%s body=%s",
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
            logger.warning("Timeout produtos API %s após 120s", path)
            return FFHubResult(ok=False, status=0, data={}, error="timeout")
        except aiohttp.ClientError as exc:
            logger.warning("Erro de rede produtos API %s: %s", path, exc)
            return FFHubResult(ok=False, status=0, data={}, error="network")
        except Exception:  # noqa: BLE001
            logger.exception("Erro inesperado produtos API %s", path)
            return FFHubResult(ok=False, status=0, data={}, error="unexpected")

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

    async def api_status(self) -> FFHubResult:
        return await self._request("GET", "/api/status", auth=False)

    async def buy_gift(self, gift_id: str, uid: str) -> FFHubResult:
        return await self._request(
            "POST",
            "/api/buy/gift",
            {"gift_id": str(gift_id), "target_uid": str(uid)},
        )

    async def diamond_order(self, order_id: str) -> FFHubResult:
        safe = "".join(ch for ch in str(order_id) if ch.isalnum() or ch in "-_")
        return await self._request("GET", f"/api/orders/diamonds/{safe}")

    async def gift_catalog(self, *, force: bool = False) -> list[GiftItem]:
        """Catálogo de presentes com estoque, em cache por alguns minutos."""
        now = time.monotonic()
        if (
            not force
            and self._catalog_cache is not None
            and now - self._catalog_at < CATALOG_TTL
        ):
            return self._catalog_cache

        result = await self._request("GET", "/api/gifts/catalog", auth=False)
        if not result.ok:
            # Mantém o último catálogo válido se a consulta falhar.
            return self._catalog_cache or []

        raw_items = result.data.get("catalogo") or result.data.get("catalog") or []
        items: list[GiftItem] = []
        for raw in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict):
                continue
            gift_id = str(raw.get("gift_id", raw.get("id", ""))).strip()
            name = str(raw.get("nome") or raw.get("name") or "").strip()
            if not gift_id or not name:
                continue
            try:
                cost = Decimal(str(raw.get("preco", raw.get("price", 0)) or 0))
            except (InvalidOperation, ValueError):
                cost = Decimal("0")
            items.append(
                GiftItem(
                    gift_id=gift_id,
                    category=str(raw.get("categoria") or raw.get("category") or "Outros").strip() or "Outros",
                    name=name,
                    cost=cost,
                    disabled=_truthy(raw.get("desativado", raw.get("disabled", False))),
                )
            )

        stock = await self._request("GET", "/api/gifts/stock", auth=False)
        if stock.ok:
            rows = stock.data.get("estoque") or stock.data.get("stock") or []
            by_id = {i.gift_id: i for i in items}
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                gid = str(row.get("gift_id", row.get("id", ""))).strip()
                item = by_id.get(gid)
                if not item:
                    continue
                qty = _to_int(
                    _find_value(row, ("quantidade", "quantity", "estoque", "stock", "disponivel_qtd"))
                )
                avail = row.get("disponivel", row.get("available", row.get("disponibilidade")))
                if qty is not None:
                    item.stock = qty
                if avail is not None and not _truthy(avail):
                    item.disabled = True
                if qty is not None and qty <= 0:
                    item.disabled = True

        self._catalog_cache = items
        self._catalog_at = now
        return items

    async def gift(self, gift_id: str) -> GiftItem | None:
        for item in await self.gift_catalog():
            if item.gift_id == str(gift_id):
                return item
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "sim", "yes", "ok", "disponivel", "disponível", "available"}
    return bool(value)


# ---------------------------------------------------------------------------
# Erros públicos: nunca expõem provedor, URL, dono ou detalhes da API.
# ---------------------------------------------------------------------------
GENERIC_UNAVAILABLE = (
    "O serviço está temporariamente indisponível. Tente novamente mais tarde."
)

_BALANCE_HINTS = ("saldo", "balance", "insufficient", "insuficiente", "funds")
_TOKEN_HINTS = ("token",)
_UID_HINTS = ("uid", "target", "jogador", "player", "conta", "account")


def _error_text(result: FFHubResult) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    parts = [
        str(result.error or ""),
        str(data.get("erro") or ""),
        str(data.get("error") or ""),
        str(data.get("message") or ""),
        str(data.get("mensagem") or ""),
    ]
    return " ".join(parts).lower()


def failure_kind(result: FFHubResult) -> str:
    """Classifica a falha para decidir reembolso/nova tentativa.

    - ``config``: chave ausente/recusada ou saldo da loja insuficiente.
    - ``invalid``: dados do cliente inválidos (UID/token).
    - ``forbidden``: operação bloqueada.
    - ``not_found``: item/pedido inexistente.
    - ``unavailable``: produto indisponível.
    - ``unconfirmed``: pode ter sido processado (timeout/502); exige análise.
    - ``unknown``: qualquer outra coisa (tratado como análise manual).
    """
    text = _error_text(result)
    status = int(result.status or 0)
    if result.error == "not_configured" or status == 401:
        return "config"
    if status == 400:
        if any(h in text for h in _BALANCE_HINTS):
            return "config"
        return "invalid"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 503:
        return "unavailable"
    if status == 502 or result.error in {"timeout", "network"}:
        return "unconfirmed"
    if 200 <= status < 300:
        # HTTP 200 com success=false: a operação não foi concluída.
        if any(h in text for h in _BALANCE_HINTS):
            return "config"
        if any(h in text for h in _TOKEN_HINTS + _UID_HINTS) and any(
            w in text for w in ("inval", "não encontrado", "nao encontrado", "not found", "expir")
        ):
            return "invalid"
        return "unknown"
    return "unknown"


def failure_is_safe_to_refund(result: FFHubResult) -> bool:
    """True quando há garantia razoável de que nada foi debitado/entregue."""
    return failure_kind(result) in {
        "config",
        "invalid",
        "forbidden",
        "not_found",
        "unavailable",
    }


def public_error(result: FFHubResult, *, field: str = "UID") -> str:
    """Mensagem formal para o cliente, sem nenhum detalhe da integração."""
    kind = failure_kind(result)
    if kind == "invalid":
        if field.lower() == "token":
            return (
                "O token informado é inválido ou expirou. "
                "Gere um novo token e tente novamente."
            )
        return "O UID informado é inválido ou não pôde ser localizado."
    if kind == "forbidden":
        return "Esta operação não é permitida para a conta informada."
    if kind == "not_found":
        return "O produto solicitado não foi encontrado."
    if kind == "unavailable":
        return "Este produto está temporariamente indisponível."
    if kind == "unconfirmed":
        return (
            "Não foi possível confirmar a conclusão da operação. "
            "Seu pedido foi encaminhado para análise da equipe."
        )
    if kind == "config":
        return GENERIC_UNAVAILABLE
    return "Não foi possível concluir a operação. Tente novamente mais tarde."


def admin_error(result: FFHubResult) -> str:
    """Detalhe curto para os donos (nunca enviar a clientes)."""
    data = result.data if isinstance(result.data, dict) else {}
    detail = (
        data.get("erro")
        or data.get("error")
        or data.get("message")
        or data.get("mensagem")
        or result.error
        or "sem detalhe"
    )
    return f"HTTP {result.status or '—'} · {failure_kind(result)} · {str(detail)[:300]}"
