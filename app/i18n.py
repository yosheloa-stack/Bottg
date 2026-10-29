"""Idiomas do menu principal (português, espanhol e inglês)."""
from __future__ import annotations

from app import texts
from app.utils import format_price

DEFAULT_LANG = "pt"

# Ordem de exibição no seletor de idioma.
LANGUAGES: dict[str, str] = {
    "pt": "🇧🇷 Português",
    "es": "🇪🇸 Español",
    "en": "🇺🇸 English",
}

WELCOME: dict[str, str] = {
    "pt": texts.WELCOME,
    "es": texts.WELCOME_ES,
    "en": texts.WELCOME_EN,
}

# Pergunta mostrada no primeiro /start, antes de o usuário escolher.
LANGUAGE_FIRST = (
    "🌐 <b>Escolha seu idioma</b>\n"
    "🌐 <b>Elige tu idioma</b>\n"
    "🌐 <b>Choose your language</b>"
)

STRINGS: dict[str, dict[str, str]] = {
    "pt": {
        "balance": "👛 Saldo: <b>R$ {balance}</b>",
        "reseller": "🤝 Revenda: <b>{status}</b>",
        "reseller_on": "Ativa ✅",
        "reseller_off": "Não ativa",
        "btn_products": "Produtos",
        "products_title": "<b>PRODUTOS</b>\n━━━━━━━━━━━━━━━━━━━━\nEscolha o serviço que deseja comprar:",
        "btn_auto220": "❤️ Auto Like 220",
        "btn_auto500": "💎 Auto Like 500",
        "btn_passe": "🎟️ Passe de Elite",
        "btn_reseller": "🤝 Revenda",
        "btn_balance": "💰 Adicionar saldo",
        "btn_rent": "🤖 Alugar bot",
        "btn_giftcard": "🎁 Resgatar Gift Card",
        "btn_orders": "📦 Meus pedidos",
        "btn_support": "🆘 Suporte",
        "btn_language": "🌐 Idioma",
        "btn_back": "⬅️ Menu",
        "language_title": (
            "🌐 <b>IDIOMA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Escolha o idioma do menu:"
        ),
        "language_saved": "✅ Idioma alterado para Português.",
        "menu_private_only": "ℹ️ O menu de compras funciona no privado do bot.",
        "cancelled": "Cancelado",
    },
    "es": {
        "balance": "👛 Saldo: <b>R$ {balance}</b>",
        "reseller": "🤝 Reventa: <b>{status}</b>",
        "reseller_on": "Activa ✅",
        "reseller_off": "No activa",
        "btn_products": "Productos",
        "products_title": "<b>PRODUCTOS</b>\n━━━━━━━━━━━━━━━━━━━━\nElige el servicio que deseas comprar:",
        "btn_auto220": "❤️ Auto Like 220",
        "btn_auto500": "💎 Auto Like 500",
        "btn_passe": "🎟️ Pase Élite",
        "btn_reseller": "🤝 Reventa",
        "btn_balance": "💰 Agregar saldo",
        "btn_rent": "🤖 Alquilar bot",
        "btn_giftcard": "🎁 Canjear Gift Card",
        "btn_orders": "📦 Mis pedidos",
        "btn_support": "🆘 Soporte",
        "btn_language": "🌐 Idioma",
        "btn_back": "⬅️ Menú",
        "language_title": (
            "🌐 <b>IDIOMA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Elige el idioma del menú:"
        ),
        "language_saved": "✅ Idioma cambiado a Español.",
        "menu_private_only": "ℹ️ El menú de compras funciona en el chat privado del bot.",
        "cancelled": "Cancelado",
    },
    "en": {
        "balance": "👛 Balance: <b>R$ {balance}</b>",
        "reseller": "🤝 Reseller: <b>{status}</b>",
        "reseller_on": "Active ✅",
        "reseller_off": "Not active",
        "btn_products": "Products",
        "products_title": "<b>PRODUCTS</b>\n━━━━━━━━━━━━━━━━━━━━\nChoose the service you want to buy:",
        "btn_auto220": "❤️ Auto Like 220",
        "btn_auto500": "💎 Auto Like 500",
        "btn_passe": "🎟️ Elite Pass",
        "btn_reseller": "🤝 Reseller",
        "btn_balance": "💰 Add balance",
        "btn_rent": "🤖 Rent bot",
        "btn_giftcard": "🎁 Redeem Gift Card",
        "btn_orders": "📦 My orders",
        "btn_support": "🆘 Support",
        "btn_language": "🌐 Language",
        "btn_back": "⬅️ Menu",
        "language_title": (
            "🌐 <b>LANGUAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Choose the menu language:"
        ),
        "language_saved": "✅ Language changed to English.",
        "menu_private_only": "ℹ️ The shopping menu works in the bot's private chat.",
        "cancelled": "Cancelled",
    },
}


def normalize_lang(code: str | None) -> str:
    """Converte 'pt-br', 'es-419', 'en-US' etc. para pt/es/en."""
    base = (code or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return base if base in LANGUAGES else DEFAULT_LANG


def t(lang: str, key: str, **kwargs) -> str:
    value = STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key)
    if value is None:
        value = STRINGS[DEFAULT_LANG][key]
    return value.format(**kwargs) if kwargs else value


async def user_lang(db, user) -> str:
    """Idioma escolhido pelo usuário; sem escolha, segue o idioma do Telegram."""
    if user is None:
        return DEFAULT_LANG
    saved = await db.get_user_language(user.id)
    if saved in LANGUAGES:
        return saved
    return normalize_lang(getattr(user, "language_code", None))


async def welcome_text(db, shop_name: str, user_id: int, lang: str) -> str:
    """Tela inicial do cliente com saldo e status de revenda no idioma dele."""
    balance_cents = await db.get_balance_cents(user_id)
    reseller = await db.is_reseller(user_id)
    status = t(lang, "reseller_on") if reseller else t(lang, "reseller_off")
    return (
        WELCOME.get(lang, WELCOME[DEFAULT_LANG]).format(shop_name=shop_name)
        + "\n\n"
        + t(lang, "balance", balance=format_price(balance_cents / 100))
        + "\n"
        + t(lang, "reseller", status=status)
    )
