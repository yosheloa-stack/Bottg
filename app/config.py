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
    likes_api_base_url: str
    likes_api_key: str
    likes_quantity: int
    autosystem_base_url: str
    autosystem_api_key: str
    passe_api_key: str
    ffhub_base_url: str
    ffhub_api_key: str
    default_region: str

    mp_access_token: str
    mp_webhook_secret: str

    efi_client_id: str
    efi_client_secret: str
    efi_pix_key: str
    efi_cert_path: str
    efi_cert_pem_base64: str
    efi_sandbox: bool
    efi_webhook_token: str

    webhook_public_url: str
    webhook_host: str
    webhook_port: int

    db_path: str

    shop_name: str
    support_username: str
    menu_banner: str  # caminho do GIF/imagem do menu

    products: dict[str, Product] = field(default_factory=dict)

    @property
    def payment_webhook_path(self) -> str:
        return "/webhook/efi"

    @property
    def payment_notification_url(self) -> str:
        base = self.webhook_public_url.rstrip("/")
        return f"{base}{self.payment_webhook_path}"


def load_config() -> Config:
    price_passe = Decimal(_get("PRICE_PASSE", "4.00"))
    price_like2_single = Decimal(_get("PRICE_LIKE2_SINGLE", "0.00"))
    price_like2_7d = Decimal(_get("PRICE_LIKE2_7D", "0.00"))
    price_like2_15d = Decimal(_get("PRICE_LIKE2_15D", "0.00"))
    price_like2_30d = Decimal(_get("PRICE_LIKE2_30D", "0.00"))

    likes_quantity = int(_get("LIKES_QUANTITY", "100"))
    if not 1 <= likes_quantity <= 200:
        raise RuntimeError("LIKES_QUANTITY deve estar entre 1 e 200.")

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
        "like2_single": Product(
            code="like2_single",
            title="💎 Like2 • Envio único",
            description=(
                "Um envio pago de likes pela FFHub. "
                "Após o PIX ser aprovado, o bot solicita seu UID e envia automaticamente."
            ),
            price=price_like2_single,
            delivery="ffhub_like_once",
        ),
        "like2_7d": Product(
            code="like2_7d",
            title="💎 Auto-Like Premium • 7 dias",
            description=(
                "Likes automáticos pela FFHub por <b>7 dias</b>. "
                "Após o PIX ser aprovado, o bot solicita seu UID e inicia os envios diários."
            ),
            price=price_like2_7d,
            delivery="ffhub_autolike",
            days=7,
        ),
        "like2_15d": Product(
            code="like2_15d",
            title="💎 Auto-Like Premium • 15 dias",
            description=(
                "Likes automáticos pela FFHub por <b>15 dias</b>. "
                "Após o PIX ser aprovado, o bot solicita seu UID e inicia os envios diários."
            ),
            price=price_like2_15d,
            delivery="ffhub_autolike",
            days=15,
        ),
        "like2_30d": Product(
            code="like2_30d",
            title="💎 Auto-Like Premium • 30 dias",
            description=(
                "Likes automáticos pela FFHub por <b>30 dias</b>. "
                "Após o PIX ser aprovado, o bot solicita seu UID e inicia os envios diários."
            ),
            price=price_like2_30d,
            delivery="ffhub_autolike",
            days=30,
        ),
    }

    return Config(
        bot_token=_get("BOT_TOKEN", required=True),
        admin_ids=[8204579375, 8669377135],
        api_base_url=_get("API_BASE_URL", "https://fluxggx.squareweb.app"),
        autolike_api_key=_get("AUTOLIKE_API_KEY", ""),
        likes_api_base_url=_get(
            "LIKES_API_BASE_URL", "http://likespainel.squareweb.app"
        ),
        likes_api_key=_get("LIKES_API_KEY", required=True),
        likes_quantity=likes_quantity,
        autosystem_base_url=_get(
            "AUTOSYSTEM_BASE_URL", "https://autolikesystem.com.br"
        ),
        autosystem_api_key=_get("AUTOSYSTEM_API_KEY", ""),
        passe_api_key=_get("PASSE_API_KEY", ""),
        ffhub_base_url=_get("FFHUB_BASE_URL", "https://ffhub-shop.shardweb.app"),
        ffhub_api_key=_get("FFHUB_API_KEY", ""),
        default_region=_get("DEFAULT_REGION", "BR"),
        mp_access_token=_get("MERCADOPAGO_ACCESS_TOKEN", ""),
        mp_webhook_secret=_get("MERCADOPAGO_WEBHOOK_SECRET", ""),
        efi_client_id=_get("EFI_CLIENT_ID", ""),
        efi_client_secret=_get("EFI_CLIENT_SECRET", ""),
        efi_pix_key=_get("EFI_PIX_KEY", ""),
        efi_cert_path=_get("EFI_CERT_PATH", ""),
        efi_cert_pem_base64=_get("EFI_CERT_PEM_BASE64", ""),
        efi_sandbox=_get("EFI_SANDBOX", "false").lower() in {"1", "true", "yes", "on"},
        efi_webhook_token=_get("EFI_WEBHOOK_TOKEN", ""),
        webhook_public_url=_get("WEBHOOK_PUBLIC_URL", ""),
        webhook_host=_get("WEBHOOK_HOST", "0.0.0.0"),
        # Square Cloud injeta a porta via PORT em deploys de site; usamos como fallback
        webhook_port=int(_get("WEBHOOK_PORT", _get("PORT", "8080"))),
        db_path=_get("DB_PATH", str(BASE_DIR / "data" / "bot.db")),
        shop_name=_get("SHOP_NAME", "Aurora System"),
        support_username=_get("SUPPORT_USERNAME", "@seu_suporte"),
        menu_banner=_get("MENU_BANNER", str(BASE_DIR / "assets" / "menu.gif")),
        products=products,
    )
