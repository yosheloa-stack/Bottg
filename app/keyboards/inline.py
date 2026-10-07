"""Teclados inline (botões) do bot — estilo profissional."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.catalog import ResolvedProduct


def _price(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Menu principal simples e direto."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🛒 Comprar", callback_data="menu:store"),
        InlineKeyboardButton(text="🔎 Consultar ID", callback_data="menu:info"),
    )
    kb.row(
        InlineKeyboardButton(text="📦 Meus pedidos", callback_data="menu:orders"),
        InlineKeyboardButton(text="❤️ Como enviar likes", callback_data="menu:like_info"),
    )
    kb.row(
        InlineKeyboardButton(text="🆘 Suporte", callback_data="menu:support")
    )
    if is_admin:
        kb.row(
            InlineKeyboardButton(text="⚙️ Painel administrativo", callback_data="admin:panel")
        )
    return kb.as_markup()


def store_menu(products: dict[str, ResolvedProduct]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for rp in products.values():
        if rp.available:
            label = f"{rp.title}  •  R$ {_price(rp.price)}"
        else:
            label = f"{rp.title}  •  ❌ Esgotado"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"buy:{rp.code}"))
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="menu:home"))
    return kb.as_markup()


def premium_like_menu(products: dict[str, ResolvedProduct]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code in ("like2_7d", "like2_15d", "like2_30d"):
        rp = products.get(code)
        if not rp:
            continue
        if rp.available:
            label = f"{rp.title}  •  R$ {_price(rp.price)}"
        else:
            label = f"{rp.title}  •  ⚠️ Indisponível"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"buy:{rp.code}"))
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="menu:home"))
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


# ---------- Admin ----------
def admin_panel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🛠️ Gerenciar produtos", callback_data="admin:products"))
    kb.row(
        InlineKeyboardButton(text="📊 Estatísticas", callback_data="admin:stats"),
        InlineKeyboardButton(text="📦 Estoque API", callback_data="admin:estoque"),
    )
    kb.row(InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast"))
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="menu:home"))
    return kb.as_markup()


def admin_products(products: dict[str, ResolvedProduct]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for rp in products.values():
        kb.row(
            InlineKeyboardButton(
                text=f"{rp.title}  •  R$ {_price(rp.price)}  •  📦 {rp.stock_label}",
                callback_data=f"admprod:{rp.code}",
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="admin:panel"))
    return kb.as_markup()


def admin_product_edit(code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="💰 Alterar preço", callback_data=f"admprice:{code}"),
        InlineKeyboardButton(text="📦 Alterar estoque", callback_data=f"admstock:{code}"),
    )
    kb.row(InlineKeyboardButton(text="♾️ Estoque ilimitado", callback_data=f"admunlim:{code}"))
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="admin:products"))
    return kb.as_markup()
