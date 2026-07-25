"""Gateway de pagamento Mercado Pago (PIX).

Usa a API REST do Mercado Pago diretamente (sem SDK) via aiohttp.
Docs: https://www.mercadopago.com.br/developers/pt/reference/payments/_payments/post
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import aiohttp

from app.services.payments.base import PaymentGateway, PaymentStatus, PixCharge

logger = logging.getLogger(__name__)

API_URL = "https://api.mercadopago.com"
TIMEOUT = aiohttp.ClientTimeout(total=30)

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


class MercadoPagoGateway(PaymentGateway):
    def __init__(self, access_token: str, notification_url: str = "") -> None:
        self._token = access_token
        self._notification_url = notification_url

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
        payload = {
            "transaction_amount": float(amount),
            "description": description,
            "payment_method_id": "pix",
            "external_reference": external_reference,
            "payer": {
                "email": payer_email or "cliente@bottg.com",
                "first_name": payer_name or "Cliente",
            },
        }
        if self._notification_url:
            payload["notification_url"] = self._notification_url

        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{API_URL}/v1/payments",
                json=payload,
                headers=self._headers(idempotency_key=str(uuid.uuid4())),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    logger.error("Falha ao criar PIX no MP (%s): %s", resp.status, data)
                    raise RuntimeError(
                        f"Mercado Pago retornou {resp.status}: "
                        f"{data.get('message', 'erro desconhecido')}"
                    )

        poi = (data.get("point_of_interaction") or {}).get("transaction_data") or {}
        return PixCharge(
            payment_id=str(data["id"]),
            status=_STATUS_MAP.get(data.get("status", ""), PaymentStatus.PENDING),
            qr_code=poi.get("qr_code", ""),
            qr_code_base64=poi.get("qr_code_base64", ""),
            ticket_url=poi.get("ticket_url", ""),
            amount=amount,
        )

    async def get_status(self, payment_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(
                f"{API_URL}/v1/payments/{payment_id}",
                headers=self._headers(),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "get_status MP retornou %s para pagamento %s",
                        resp.status,
                        payment_id,
                    )
                    return PaymentStatus.UNKNOWN
                data = await resp.json()
        return _STATUS_MAP.get(data.get("status", ""), PaymentStatus.UNKNOWN)

    async def parse_webhook(self, query: dict, body: dict) -> str | None:
        # O Mercado Pago envia notificações de formas diferentes:
        # 1) query: ?type=payment&data.id=123  ou  ?topic=payment&id=123
        # 2) body: {"type": "payment", "data": {"id": "123"}}
        topic = (
            query.get("type")
            or query.get("topic")
            or (body.get("type") if isinstance(body, dict) else None)
            or (body.get("action", "").split(".")[0] if isinstance(body, dict) else None)
        )
        if topic and "payment" not in str(topic):
            return None

        payment_id = (
            query.get("data.id")
            or query.get("id")
            or (body.get("data", {}) or {}).get("id")
        )
        return str(payment_id) if payment_id else None
