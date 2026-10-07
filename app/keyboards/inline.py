"""Teclados inline (botões) do bot — estilo profissional."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.catalog import ResolvedProduct


def _price(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Menu principal organizado por ação."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🛒 Comprar", callback_data="menu:store"),
        InlineKeyboardButton(text="📦 Meus pedidos", callback_data="menu:orders"),
    )
    kb.row(
        InlineKeyboardButton(text="🔎 Consultar ID", callback_data="menu:info"),
        InlineKeyboardButton(text="🆔 Meu ID", callback_data="menu:my_id"),
    )
    kb.row(
        InlineKeyboardButton(text="❤️ Como funciona", callback_data="menu:like_info"),
        InlineKeyboardButton(text="🆘 Suporte", callback_data="menu:support"),
    )
    if is_admin:
        kb.row(
            InlineKeyboardButton(text="⚙️ Painel Admin", callback_data="admin:panel")
        )
    return kb.as_markup()


def store_menu(products: dict[str, ResolvedProduct]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # Produtos normais aparecem direto na loja.
    for rp in products.values():
        if rp.code.startswith("like2_"):
            continue
        if rp.price <= 0:
            continue
        if rp.available:
            label = f"{rp.title} • R$ {_price(rp.price)}"
        else:
            label = f"{rp.title} • Esgotado"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"buy:{rp.code}"))

    # Auto Like fica agrupado em uma categoria própria.
    kb.row(
        InlineKeyboardButton(
            text="💎 Auto Like 500/1000",
            callback_data="menu:autolike",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="menu:home"))
    return kb.as_markup()


def autolike_plans_menu(products: dict[str, ResolvedProduct]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code in ("like2_7d", "like2_15d", "like2_30d"):
        rp = products.get(code)
        if not rp:
            continue

        days = rp.product.days
        if rp.available:
            label = f"📆 {days} dias • R$ {_price(rp.price)}"
            callback = f"buy:{rp.code}"
        elif rp.price <= 0:
            label = f"📆 {days} dias • Indisponível"
            callback = "noop:unavailable"
        else:
            label = f"📆 {days} dias • Esgotado"
            callback = "noop:unavailable"

        kb.row(InlineKeyboardButton(text=label, callback_data=callback))

    kb.row(InlineKeyboardButton(text="⬅️ Voltar à loja", callback_data="menu:store"))
    return kb.as_markup()


def premium_like_menu(
    products: dict[str, ResolvedProduct],
    *,
    is_owner: bool = False,
    ffhub_configured: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if is_owner and not ffhub_configured:
        kb.row(
            InlineKeyboardButton(
                text="🔑 Configurar FFHub API",
                callback_data="owner:set:FFHUB_API_KEY",
            )
        )

    for code in ("like2_single", "like2_7d", "like2_15d", "like2_30d"):
        rp = products.get(code)
        if not rp:
            continue

        if rp.price <= 0:
            if is_owner:
                kb.row(
                    InlineKeyboardButton(
                        text=f"💰 {rp.title} • Definir preço",
                        callback_data=f"admprod:{rp.code}",
                    )
                )
            continue

        if rp.available:
            kb.row(
                InlineKeyboardButton(
                    text=f"{rp.title}  •  R$ {_price(rp.price)}",
                    callback_data=f"buy:{rp.code}",
                )
            )
            continue

        if is_owner:
            kb.row(
                InlineKeyboardButton(
                    text=f"⚙️ {rp.title} • Configurar",
                    callback_data=f"admprod:{rp.code}",
                )
            )

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
    kb.row(
        InlineKeyboardButton(
            text="🔐 Configurações do dono",
            callback_data="owner:settings",
        )
    )
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


def owner_settings_panel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="💎 FFHub API Key", callback_data="owner:set:FFHUB_API_KEY")
    )
    kb.row(
        InlineKeyboardButton(text="❤️ Likes API Key", callback_data="owner:set:LIKES_API_KEY"),
        InlineKeyboardButton(text="🔁 AutoSystem Key", callback_data="owner:set:AUTOSYSTEM_API_KEY"),
    )
    kb.row(
        InlineKeyboardButton(text="🎟️ Passe API Key", callback_data="owner:set:PASSE_API_KEY")
    )
    kb.row(
        InlineKeyboardButton(text="🏦 Efí Client ID", callback_data="owner:set:EFI_CLIENT_ID"),
        InlineKeyboardButton(text="🔑 Efí Secret", callback_data="owner:set:EFI_CLIENT_SECRET"),
    )
    kb.row(
        InlineKeyboardButton(text="💠 Efí Chave PIX", callback_data="owner:set:EFI_PIX_KEY")
    )
    kb.row(InlineKeyboardButton(text="⬅️ Painel Admin", callback_data="admin:panel"))
    return kb.as_markup()
