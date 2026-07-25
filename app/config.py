"""Carregamento e validação da configuração a partir de variáveis de ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value or ""


def _get_int_list(name: str) -> list[int]:
    raw = os.getenv(name, "")
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


@dataclass(frozen=True)
class Product:
    """Um produto vendável na loja."""

    code: str
    title: str
    description: str
    price: Decimal
    # Ação de entrega: 'passe' ou 'autolike'
    delivery: str
    # Dias (usado por assinaturas auto-like)
    days: int = 0


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]

    api_base_url: str
    autolike_api_key: str
    passe_api_key: str
    default_region: str

    mp_access_token: str
    mp_webhook_secret: str

    webhook_public_url: str
    webhook_host: str
    webhook_port: int

    db_path: str

    products: dict[str, Product] = field(default_factory=dict)

    @property
    def mp_webhook_path(self) -> str:
        return "/webhook/mercadopago"

    @property
    def mp_notification_url(self) -> str:
        base = self.webhook_public_url.rstrip("/")
        return f"{base}{self.mp_webhook_path}"


def load_config() -> Config:
    price_passe = Decimal(_get("PRICE_PASSE", "19.90"))
    price_autolike = Decimal(_get("PRICE_AUTOLIKE_30D", "14.90"))

    products = {
        "passe": Product(
            code="passe",
            title="🎟️ Passe Booyah",
            description=(
                "Entrega automática de um <b>Passe Booyah</b> na sua conta "
                "de Free Fire. Basta informar seu ID."
            ),
            price=price_passe,
            delivery="passe",
        ),
        "autolike_30d": Product(
            code="autolike_30d",
            title="🔁 Auto-Like (30 dias)",
            description=(
                "Assinatura de <b>likes automáticos diários</b> por 30 dias. "
                "Seu ID recebe likes todos os dias sem precisar pedir."
            ),
            price=price_autolike,
            delivery="autolike",
            days=30,
        ),
    }

    return Config(
        bot_token=_get("BOT_TOKEN", required=True),
        admin_ids=_get_int_list("ADMIN_IDS"),
        api_base_url=_get("API_BASE_URL", "https://fluxggx.squareweb.app"),
        autolike_api_key=_get("AUTOLIKE_API_KEY", required=True),
        passe_api_key=_get("PASSE_API_KEY", ""),
        default_region=_get("DEFAULT_REGION", "BR"),
        mp_access_token=_get("MERCADOPAGO_ACCESS_TOKEN", required=True),
        mp_webhook_secret=_get("MERCADOPAGO_WEBHOOK_SECRET", ""),
        webhook_public_url=_get("WEBHOOK_PUBLIC_URL", ""),
        webhook_host=_get("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=int(_get("WEBHOOK_PORT", "8080")),
        db_path=_get("DB_PATH", str(BASE_DIR / "data" / "bot.db")),
        products=products,
    )
