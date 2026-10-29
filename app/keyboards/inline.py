"""Teclados inline (botões) do bot — estilo profissional."""
from __future__ import annotations

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.catalog import ResolvedProduct
from app.i18n import DEFAULT_LANG, LANGUAGES, t


def _price(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def main_menu(is_admin: bool = False, lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """Menu inicial exclusivo para clientes, no idioma do usuário.

    is_admin é mantido por compatibilidade, mas não exibe controles de dono.
    """
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=t(lang, "btn_products"), callback_data="menu:products"))
    kb.row(
        InlineKeyboardButton(text=t(lang, "btn_balance"), callback_data="shop:balance"),
        InlineKeyboardButton(text=t(lang, "btn_giftcard"), callback_data="shop:giftcard"),
    )
    kb.row(
        InlineKeyboardButton(text=t(lang, "btn_orders"), callback_data="menu:orders"),
        InlineKeyboardButton(text=t(lang, "btn_support"), callback_data="menu:support"),
    )
    kb.row(InlineKeyboardButton(text=t(lang, "btn_language"), callback_data="menu:lang"))
    return kb.as_markup()


def products_menu(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """Lista de produtos aberta pelo botão Produtos do menu inicial."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=t(lang, "btn_auto220"), callback_data="shop:auto220"),
        InlineKeyboardButton(text=t(lang, "btn_auto500"), callback_data="shop:auto500"),
    )
    kb.row(
        InlineKeyboardButton(text=t(lang, "btn_passe"), callback_data="nav:v2:passe"),
        InlineKeyboardButton(text=t(lang, "btn_reseller"), callback_data="shop:reseller"),
    )
    kb.row(InlineKeyboardButton(text=t(lang, "btn_rent"), callback_data="shop:rent"))
    kb.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="nav:v2:home"))
    return kb.as_markup()


def language_menu(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, label in LANGUAGES.items():
        text = f"» {label} «" if code == lang else label
        kb.row(InlineKeyboardButton(text=text, callback_data=f"lang:{code}"))
    kb.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="nav:v2:home"))
    return kb.as_markup()


def autolike_days_menu(product_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a, b in ((7, 15), (30, 60)):
        kb.row(
            InlineKeyboardButton(
                text=f"📆 {a} dias",
                callback_data=f"days:{product_code}:{a}",
            ),
            InlineKeyboardButton(
                text=f"📆 {b} dias",
                callback_data=f"days:{product_code}:{b}",
            ),
        )
    kb.row(
        InlineKeyboardButton(
            text="📆 90 dias",
            callback_data=f"days:{product_code}:90",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="✏️ Escolher outro período",
            callback_data=f"dayscustom:{product_code}",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home"))
    return kb.as_markup()


def checkout_menu(
    product_code: str,
    *,
    ref_value: str = "",
    allow_balance: bool = True,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    suffix = f":{ref_value}" if ref_value else ""
    kb.row(
        InlineKeyboardButton(
            text="💠 Pagar com PIX",
            callback_data=f"paypix:{product_code}{suffix}",
        )
    )
    from app.binance_pay import cached_available

    if cached_available():
        kb.row(
            InlineKeyboardButton(
                text="Pagar com Binance (USDT)",
                callback_data=f"paybnc:{product_code}{suffix}",
            )
        )
    if allow_balance:
        kb.row(
            InlineKeyboardButton(
                text="💰 Pagar com saldo",
                callback_data=f"paybal:{product_code}{suffix}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="nav:v2:home"))
    return kb.as_markup()


def reseller_area_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="❤️ Auto Like 220", callback_data="shop:auto220"),
        InlineKeyboardButton(text="💎 Auto Like 500", callback_data="shop:auto500"),
    )
    kb.row(
        InlineKeyboardButton(text="🎟️ Passe de Elite", callback_data="nav:v2:passe"),
        InlineKeyboardButton(text="🤖 Alugar bot", callback_data="shop:rent"),
    )
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home"))
    return kb.as_markup()


def add_group_button(url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Adicionar bot ao grupo", url=url))
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home"))
    return kb.as_markup()


def payment_pending(payment_id: str, pix_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📋 Copiar código PIX",
            copy_text=CopyTextButton(text=pix_code),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🔄 Já paguei / verificar",
            callback_data=f"check:{payment_id}",
        )
    )
    return kb.as_markup()


def back_home() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home"))
    return kb.as_markup()


def cancel_only() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Cancelar", callback_data="cancel"))
    return kb.as_markup()
