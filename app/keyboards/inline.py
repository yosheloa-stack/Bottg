"""Teclados inline (botões) do bot — estilo profissional."""
from __future__ import annotations

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.catalog import ResolvedProduct


def _price(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Menu inicial exclusivo para clientes.

    is_admin é mantido por compatibilidade, mas não exibe controles de dono.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="❤️ Auto Like 220", callback_data="shop:auto220"),
        InlineKeyboardButton(text="💎 Auto Like 500", callback_data="shop:auto500"),
    )
    kb.row(
        InlineKeyboardButton(text="🎟️ Passe de Elite", callback_data="nav:v2:passe"),
        InlineKeyboardButton(text="🤝 Revenda", callback_data="shop:reseller"),
    )
    kb.row(
        InlineKeyboardButton(text="💰 Adicionar saldo", callback_data="shop:balance"),
        InlineKeyboardButton(text="🤖 Alugar bot", callback_data="shop:rent"),
    )
    kb.row(
        InlineKeyboardButton(text="📦 Meus pedidos", callback_data="menu:orders"),
        InlineKeyboardButton(text="🆘 Suporte", callback_data="menu:support"),
    )
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
        InlineKeyboardButton(text="💎 VIP", callback_data="admin:vip"),
        InlineKeyboardButton(text="🔥 VIP2", callback_data="admin:vip2"),
    )
    kb.row(
        InlineKeyboardButton(
            text="🔐 Configurações",
            callback_data="owner:settings",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home"))
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
        InlineKeyboardButton(
            text="💳 Mercado Pago Access Token",
            callback_data="owner:set:MERCADOPAGO_ACCESS_TOKEN",
        )
    )
    kb.row(InlineKeyboardButton(text="⬅️ Painel Admin", callback_data="admin:panel"))
    return kb.as_markup()



def vip_admin_panel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="➕ Adicionar VIP", callback_data="vip:add"),
        InlineKeyboardButton(text="➖ Remover VIP", callback_data="vip:remove"),
    )
    kb.row(
        InlineKeyboardButton(text="📋 Listar VIPs", callback_data="vip:list")
    )
    kb.row(InlineKeyboardButton(text="⬅️ Painel Admin", callback_data="admin:panel"))
    return kb.as_markup()



def vip2_admin_panel() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="➕ Adicionar VIP2", callback_data="vip2:add"),
        InlineKeyboardButton(text="➖ Remover VIP2", callback_data="vip2:remove"),
    )
    kb.row(
        InlineKeyboardButton(text="📋 Listar VIP2", callback_data="vip2:list")
    )
    kb.row(InlineKeyboardButton(text="⬅️ Painel Admin", callback_data="admin:panel"))
    return kb.as_markup()
