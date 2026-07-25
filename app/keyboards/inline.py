"""Teclados inline (botões) do bot."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Config


def main_menu(config: Config, is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🛒 Loja", callback_data="menu:store"))
    kb.row(
        InlineKeyboardButton(text="❤️ Enviar Likes", callback_data="menu:like"),
        InlineKeyboardButton(text="🔎 Consultar ID", callback_data="menu:info"),
    )
    kb.row(
        InlineKeyboardButton(text="📦 Meus pedidos", callback_data="menu:orders"),
        InlineKeyboardButton(text="🆘 Suporte", callback_data="menu:support"),
    )
    if is_admin:
        kb.row(InlineKeyboardButton(text="⚙️ Painel Admin", callback_data="admin:panel"))
    return kb.as_markup()


def store_menu(config: Config) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for product in config.products.values():
        price = f"{product.price:.2f}".replace(".", ",")
        kb.row(
            InlineKeyboardButton(
                text=f"{product.title} — R$ {price}",
                callback_data=f"buy:{product.code}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="menu:home"))
    return kb.as_markup()


def confirm_purchase(product_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✅ Confirmar e pagar", callback_data=f"confirm:{product_code}"
        )
    )
    kb.row(InlineKeyboardButton(text="❌ Cancelar", callback_data="cancel"))
    return kb.as_markup()


def payment_pending(payment_id: str, ticket_url: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔄 Já paguei / verificar", callback_data=f"check:{payment_id}"
        )
    )
    if ticket_url:
        kb.row(InlineKeyboardButton(text="🌐 Abrir cobrança", url=ticket_url))
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="menu:home"))
    return kb.as_markup()


def back_home() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="menu:home"))
    return kb.as_markup()


def cancel_only() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Cancelar", callback_data="cancel"))
    return kb.as_markup()


def admin_panel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📊 Estatísticas", callback_data="admin:stats"),
        InlineKeyboardButton(text="📦 Estoque Passe", callback_data="admin:estoque"),
    )
    kb.row(InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast"))
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="menu:home"))
    return kb.as_markup()
