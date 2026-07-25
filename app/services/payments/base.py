"""Abstração de gateway de pagamento.

Permite trocar o provedor (Mercado Pago, PushinPay, Efí...) sem alterar
o restante do bot. Cada gateway concreto implementa esta interface.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class PixCharge:
    """Cobrança PIX criada no gateway."""

    payment_id: str
    status: PaymentStatus
    qr_code: str  # copia e cola
    qr_code_base64: str  # imagem PNG em base64 (sem prefixo data:)
    ticket_url: str
    amount: Decimal


class PaymentGateway(ABC):
    """Interface comum a todos os gateways de pagamento."""

    @abstractmethod
    async def create_pix(
        self,
        amount: Decimal,
        description: str,
        external_reference: str,
        payer_email: str,
        payer_name: str,
    ) -> PixCharge:
        ...

    @abstractmethod
    async def get_status(self, payment_id: str) -> PaymentStatus:
        ...

    @abstractmethod
    async def parse_webhook(self, query: dict, body: dict) -> str | None:
        """Extrai o payment_id de uma notificação de webhook. Retorna None se irrelevante."""
        ...
