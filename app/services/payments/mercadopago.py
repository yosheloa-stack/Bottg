"""Gateway de pagamento Mercado Pago (PIX).

Usa a API REST do Mercado Pago diretamente (sem SDK) via aiohttp.
Docs: https://www.mercadopago.com.br/developers/pt/reference/payments/_payments/post
"""
from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal

import aiohttp

from app.services.payments.base import PaymentGateway, PaymentStatus, PixCharge

logger = logging.getLogger(__name__)

API_URL = "https://api.mercadopago.com"
TIMEOUT = aiohttp.ClientTimeout(total=30)
# Espera mínima/máxima depois de um 429 (limite de requisições) do MP.
RATE_LIMIT_MIN_BACKOFF = 60
RATE_LIMIT_MAX_BACKOFF = 600

_STATUS_MAP = {
    "pending": PaymentStatus.PENDING,
    "in_process": PaymentStatus.PENDING,
    "authorized": PaymentStatus.PENDING,
    "approved": PaymentStatus.APPROVED,
    "rejected": PaymentStatus.REJECTED,
    "cancelled": PaymentStatus.CANCELLED,
    "refunded": PaymentStatus.CANCELLED,
    "charged_back": PaymentStatus.CANCELLED,
}

_ORDER_STATUS_MAP = {
    "created": PaymentStatus.PENDING,
    "processing": PaymentStatus.PENDING,
    "action_required": PaymentStatus.PENDING,
    "processed": PaymentStatus.APPROVED,
    "failed": PaymentStatus.REJECTED,
    "canceled": PaymentStatus.CANCELLED,
    "cancelled": PaymentStatus.CANCELLED,
    "expired": PaymentStatus.EXPIRED,
    "refunded": PaymentStatus.CANCELLED,
    "charged_back": PaymentStatus.CANCELLED,
}


class MercadoPagoGateway(PaymentGateway):
    def __init__(self, access_token: str, notification_url: str = "") -> None:
        self._token = access_token.strip()
        self._notification_url = notification_url.strip()
        # Até quando o monitor automático deve evitar novas consultas (429).
        self.rate_limited_until = 0.0
        self._rate_limit_backoff = RATE_LIMIT_MIN_BACKOFF

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    async def create_pix(
        self,
        amount: Decimal,
        description: str,
        external_reference: str,
        payer_email: str,
        payer_name: str,
    ) -> PixCharge:
        """Cria cobrança PIX pela Orders API do Mercado Pago."""
        if not self.configured:
            raise RuntimeError(
                "Mercado Pago não configurado. Informe o Access Token."
            )

        amount_text = f"{Decimal(amount).quantize(Decimal('0.01')):.2f}"
        payload = {
            "type": "online",
            "total_amount": amount_text,
            "external_reference": str(external_reference),
            "processing_mode": "automatic",
            "transactions": {
                "payments": [
                    {
                        "amount": amount_text,
                        "payment_method": {
                            "id": "pix",
                            "type": "bank_transfer",
                        },
                    }
                ]
            },
            "payer": {
                "email": payer_email or "cliente@bottg.com",
            },
        }

        idempotency_key = str(uuid.uuid4())
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{API_URL}/v1/orders",
                json=payload,
                headers=self._headers(idempotency_key=idempotency_key),
            ) as resp:
                raw = await resp.text()
                try:
                    import json
                    data = json.loads(raw) if raw else {}
                except Exception:
                    data = {"raw": raw}

                if resp.status not in (200, 201):
                    logger.error(
                        "Falha ao criar PIX Orders API MP (%s): %s",
                        resp.status,
                        data,
                    )
                    message = (
                        data.get("message")
                        or data.get("error")
                        or data.get("cause")
                        or "erro desconhecido"
                    )
                    raise RuntimeError(
                        f"Mercado Pago retornou {resp.status}: {message}"
                    )

        payments = ((data.get("transactions") or {}).get("payments") or [])
        payment = payments[0] if payments else {}
        method = payment.get("payment_method") or {}

        qr_code = method.get("qr_code") or ""
        qr_code_base64 = (
            method.get("qr_code_base64")
            or method.get("qr_code_based64")
            or ""
        )
        ticket_url = method.get("ticket_url") or ""

        if not qr_code and not ticket_url:
            logger.error("Orders API criou cobrança sem dados PIX: %s", data)
            raise RuntimeError(
                "Mercado Pago criou a cobrança, mas não retornou o PIX."
            )

        return PixCharge(
            payment_id=str(data["id"]),
            status=_ORDER_STATUS_MAP.get(
                str(data.get("status") or ""),
                PaymentStatus.PENDING,
            ),
            qr_code=qr_code,
            qr_code_base64=qr_code_base64,
            ticket_url=ticket_url,
            amount=Decimal(amount_text),
        )

    async def get_status(self, payment_id: str) -> PaymentStatus:
        if not self.configured:
            return PaymentStatus.UNKNOWN

        identifier = str(payment_id or "").strip()
        is_order = identifier.upper().startswith("ORD")
        endpoint = (
            f"{API_URL}/v1/orders/{identifier}"
            if is_order
            else f"{API_URL}/v1/payments/{identifier}"
        )

        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(
                endpoint,
                headers=self._headers(),
            ) as resp:
                if resp.status == 429:
                    self._register_rate_limit(resp.headers.get("Retry-After"))
                    logger.warning(
                        "get_status MP retornou 429 para %s; pausando consultas "
                        "automáticas por %ss",
                        identifier,
                        int(self.rate_limited_until - time.time()),
                    )
                    return PaymentStatus.UNKNOWN
                if resp.status != 200:
                    logger.warning(
                        "get_status MP retornou %s para %s",
                        resp.status,
                        identifier,
                    )
                    return PaymentStatus.UNKNOWN
                data = await resp.json(content_type=None)

        self._rate_limit_backoff = RATE_LIMIT_MIN_BACKOFF
        if is_order:
            return _ORDER_STATUS_MAP.get(
                str(data.get("status") or ""),
                PaymentStatus.UNKNOWN,
            )
        return _STATUS_MAP.get(
            str(data.get("status") or ""),
            PaymentStatus.UNKNOWN,
        )

    @property
    def rate_limited(self) -> bool:
        return time.time() < self.rate_limited_until

    def _register_rate_limit(self, retry_after: str | None) -> None:
        """Marca uma pausa após 429, respeitando Retry-After ou dobrando a espera."""
        try:
            wait = int(float(retry_after)) if retry_after else 0
        except ValueError:
            wait = 0
        wait = max(wait, self._rate_limit_backoff)
        wait = min(wait, RATE_LIMIT_MAX_BACKOFF)
        self.rate_limited_until = time.time() + wait
        self._rate_limit_backoff = min(wait * 2, RATE_LIMIT_MAX_BACKOFF)

    async def parse_webhook(self, query: dict, body: dict) -> str | None:
        """Extrai ID de payment legado ou order da notificação do Mercado Pago."""
        body = body if isinstance(body, dict) else {}
        topic = (
            query.get("type")
            or query.get("topic")
            or body.get("type")
            or body.get("topic")
            or str(body.get("action") or "").split(".")[0]
        )

        topic_text = str(topic or "").lower()
        if topic_text and (
            "payment" not in topic_text
            and "order" not in topic_text
            and "orders_v2" not in topic_text
        ):
            return None

        data = body.get("data") or {}
        identifier = (
            query.get("data.id")
            or query.get("id")
            or (data.get("id") if isinstance(data, dict) else None)
            or body.get("id")
        )
        return str(identifier) if identifier else None
