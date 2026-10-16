"""Painel administrativo: produtos (preço/estoque), estatísticas, estoque API, broadcast.

Todas as ações são restritas aos IDs em ADMIN_IDS.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.catalog import resolve_product, resolve_products
from app.config import Config
from app.database import Database
from app.owner_auth import resolve_owner_id
from app.keyboards.inline import (
    admin_panel,
    admin_product_edit,
    admin_products,
    back_home,
    owner_settings_panel,
)
from app.services.autolike import AutoLikeApi
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.ffhub_shop import FFHubShopApi
from app.services.likes import LikesApi
from app.services.passe import PasseApi
from app.services.payments import MercadoPagoGateway
from app.states import AdminBroadcast, AdminPrice, AdminStock, OwnerSetting
from app.ui import edit_screen
from app.utils import format_price

logger = logging.getLogger(__name__)

router = Router(name="admin")
OWNER_IDS = {8204579375, 8669377135}

OWNER_SETTING_LABELS = {
    "FFHUB_API_KEY": "FFHub API Key",
    "LIKES_API_KEY": "Likes API Key",
    "AUTOSYSTEM_API_KEY": "AutoSystem API Key",
    "PASSE_API_KEY": "Passe API Key",
    "MERCADOPAGO_ACCESS_TOKEN": "Mercado Pago Access Token",
}


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


async def _deny(query: CallbackQuery) -> None:
    await query.answer("Sem permissão.", show_alert=True)


# ---------- Entrada ----------
@router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config, bot: Bot) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    await message.answer("⚙️ <b>Painel Admin</b>", reply_markup=admin_panel())


@router.callback_query(F.data == "admin:panel")
async def cb_panel(query: CallbackQuery, config: Config) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    await edit_screen(query, "⚙️ <b>Painel Admin</b>", admin_panel())
    await query.answer()


# ---------- Configurações privadas do dono ----------
@router.callback_query(F.data == "owner:settings")
async def cb_owner_settings(
    query: CallbackQuery,
    config: Config,
    db: Database,
) -> None:
    if query.from_user.id not in OWNER_IDS:
        return await _deny(query)

    saved = await db.all_owner_settings()
    fallbacks = {
        "FFHUB_API_KEY": config.ffhub_api_key,
        "LIKES_API_KEY": config.likes_api_key,
        "AUTOSYSTEM_API_KEY": config.autosystem_api_key,
        "PASSE_API_KEY": config.passe_api_key,
        "MERCADOPAGO_ACCESS_TOKEN": config.mp_access_token,
    }

    lines = [
        "🔐 <b>Configurações privadas do dono</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "Os valores ficam salvos no banco e nunca são exibidos aqui.",
        "",
    ]
    for key, label in OWNER_SETTING_LABELS.items():
        configured = bool((saved.get(key) or fallbacks.get(key) or "").strip())
        lines.append(f"{'✅' if configured else '❌'} {label}")

    lines.extend(
        [
            "",
            "💰 Os preços dos planos são alterados em <b>Gerenciar produtos</b>.",
            "👇 Escolha o que deseja configurar:",
        ]
    )

    await edit_screen(query, "\n".join(lines), owner_settings_panel())
    await query.answer()


@router.callback_query(F.data.startswith("owner:set:"))
async def cb_owner_setting_start(
    query: CallbackQuery,
    state: FSMContext,
) -> None:
    if query.from_user.id not in OWNER_IDS:
        return await _deny(query)

    key = query.data.split(":", 2)[2]
    label = OWNER_SETTING_LABELS.get(key)
    if not label:
        return await query.answer("Configuração inválida.", show_alert=True)

    await state.set_state(OwnerSetting.waiting_value)
    await state.update_data(owner_setting_key=key)
    await edit_screen(
        query,
        f"🔐 <b>{label}</b>\n\n"
        "Envie o novo valor em uma mensagem.\n"
        "Ele será salvo de forma privada no banco do bot.\n\n"
        "Use /cancel para cancelar.",
        back_home(),
    )
    await query.answer()


@router.message(OwnerSetting.waiting_value)
async def owner_setting_receive(
    message: Message,
    state: FSMContext,
    db: Database,
    likes_api: LikesApi,
    autosystem_likes_api: AutoSystemLikesApi,
    ffhub_shop: FFHubShopApi,
    passe: PasseApi,
    gateway: MercadoPagoGateway,
) -> None:
    if not message.from_user or message.from_user.id not in OWNER_IDS:
        return

    value = (message.text or "").strip()
    if not value:
        await message.answer("⚠️ Valor vazio. Envie a configuração correta.")
        return

    data = await state.get_data()
    key = str(data.get("owner_setting_key") or "")
    label = OWNER_SETTING_LABELS.get(key)
    if not label:
        await state.clear()
        await message.answer("⚠️ Configuração inválida.")
        return

    await db.set_owner_setting(key, value)

    # Aplica imediatamente nas instâncias que já estão rodando.
    if key == "FFHUB_API_KEY":
        ffhub_shop._key = value
    elif key == "LIKES_API_KEY":
        likes_api._key = value
    elif key == "AUTOSYSTEM_API_KEY":
        autosystem_likes_api._key = value
    elif key == "PASSE_API_KEY":
        passe._key = value
    elif key == "MERCADOPAGO_ACCESS_TOKEN":
        gateway._token = value

    await state.clear()
    await message.answer(
        f"✅ <b>{label}</b> atualizado e salvo.\n"
        "O valor não será mostrado por segurança.",
        reply_markup=owner_settings_panel(),
    )


# ---------- Gerenciar produtos ----------
@router.callback_query(F.data == "admin:products")
async def cb_products(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    products = await resolve_products(config, db)
    await edit_screen(
        query,
        "🛠️ <b>Gerenciar produtos</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Toque em um produto para alterar <b>preço</b> ou <b>estoque</b>:",
        admin_products(products),
    )
    await query.answer()


@router.callback_query(F.data.startswith("admprod:"))
async def cb_product_edit(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    code = query.data.split(":", 1)[1]
    rp = await resolve_product(config, db, code)
    if not rp:
        return await query.answer("Produto não encontrado.", show_alert=True)
    await edit_screen(
        query,
        f"🛠️ <b>{rp.title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Preço atual: <b>R$ {format_price(rp.price)}</b>\n"
        f"📦 Estoque atual: <b>{rp.stock_label}</b>\n\n"
        "O que deseja alterar?",
        admin_product_edit(code),
    )
    await query.answer()


# ---- Alterar preço ----
@router.callback_query(F.data.startswith("admprice:"))
async def cb_price_start(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)

    code = query.data.split(":", 1)[1]
    rp = await resolve_product(config, db, code)
    if not rp:
        return await query.answer("Produto não encontrado.", show_alert=True)

    await state.set_state(AdminPrice.waiting_value)
    await state.update_data(code=code)
    await query.answer()
    await query.message.answer(
        "💰 <b>ALTERAR PREÇO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produto: <b>{rp.title}</b>\n"
        f"💵 Preço atual: <b>R$ {format_price(rp.price)}</b>\n\n"
        "Responda esta mensagem com o novo preço.\n"
        "Aceito: <code>25</code>, <code>25,00</code>, "
        "<code>25.00</code> ou <code>R$ 25,00</code>.\n\n"
        "Use /cancel para cancelar.",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Ex.: 25,00",
        ),
    )


@router.message(AdminPrice.waiting_value)
async def price_receive(
    message: Message, state: FSMContext, config: Config, db: Database
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return

    raw = (message.text or "").strip()
    normalized = (
        raw.upper()
        .replace("R$", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
    try:
        value = Decimal(normalized)
        if value <= 0:
            raise InvalidOperation
        value = value.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer(
            "⚠️ <b>Preço inválido.</b>\n"
            "Envie, por exemplo: <code>25</code>, <code>25,00</code> "
            "ou <code>25.00</code>."
        )
        return

    data = await state.get_data()
    code = str(data.get("code") or "")
    if not code:
        await state.clear()
        await message.answer(
            "⚠️ A edição de preço expirou. Abra o produto novamente no painel."
        )
        return

    await db.set_price(code, f"{value:.2f}")
    await state.clear()

    rp = await resolve_product(config, db, code)
    if not rp:
        await message.answer("⚠️ Preço salvo, mas o produto não foi localizado.")
        return

    await message.answer(
        "✅ <b>PREÇO SALVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produto: <b>{rp.title}</b>\n"
        f"💰 Novo preço: <b>R$ {format_price(rp.price)}</b>\n"
        f"📦 Estoque: <b>{rp.stock_label}</b>\n\n"
        "A alteração já está valendo na loja.",
        reply_markup=admin_product_edit(code),
    )


# ---- Alterar estoque ----
@router.callback_query(F.data.startswith("admstock:"))
async def cb_stock_start(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)

    code = query.data.split(":", 1)[1]
    rp = await resolve_product(config, db, code)
    if not rp:
        return await query.answer("Produto não encontrado.", show_alert=True)

    await state.set_state(AdminStock.waiting_value)
    await state.update_data(code=code)
    await query.answer()
    await query.message.answer(
        "📦 <b>ALTERAR ESTOQUE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produto: <b>{rp.title}</b>\n"
        f"🔢 Estoque atual: <b>{rp.stock_label}</b>\n\n"
        "Responda esta mensagem com uma quantidade inteira.\n"
        "Use <code>-1</code> para estoque ilimitado.\n\n"
        "Use /cancel para cancelar.",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Ex.: 50 ou -1",
        ),
    )


@router.message(AdminStock.waiting_value)
async def stock_receive(
    message: Message, state: FSMContext, config: Config, db: Database
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return

    raw = (message.text or "").strip().replace(" ", "")
    try:
        value = int(raw)
    except ValueError:
        await message.answer(
            "⚠️ <b>Estoque inválido.</b>\n"
            "Envie somente um número inteiro, como <code>50</code>, "
            "ou <code>-1</code> para ilimitado."
        )
        return

    if value < -1:
        await message.answer(
            "⚠️ Use <code>-1</code> para ilimitado ou um número a partir de "
            "<code>0</code>."
        )
        return

    data = await state.get_data()
    code = str(data.get("code") or "")
    if not code:
        await state.clear()
        await message.answer(
            "⚠️ A edição de estoque expirou. Abra o produto novamente no painel."
        )
        return

    await db.set_stock(code, value)
    await state.clear()

    rp = await resolve_product(config, db, code)
    if not rp:
        await message.answer("⚠️ Estoque salvo, mas o produto não foi localizado.")
        return

    await message.answer(
        "✅ <b>ESTOQUE SALVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produto: <b>{rp.title}</b>\n"
        f"🔢 Novo estoque: <b>{rp.stock_label}</b>\n"
        f"💰 Preço atual: <b>R$ {format_price(rp.price)}</b>\n\n"
        "A alteração já está valendo na loja.",
        reply_markup=admin_product_edit(code),
    )


@router.callback_query(F.data.startswith("admunlim:"))
async def cb_unlimited(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    code = query.data.split(":", 1)[1]
    await db.set_stock(code, -1)
    rp = await resolve_product(config, db, code)
    await edit_screen(
        query,
        f"♾️ Estoque de <b>{rp.title if rp else code}</b> definido como "
        "<b>Ilimitado</b>.",
        admin_product_edit(code),
    )
    await query.answer("Estoque ilimitado ✓")


# ---------- Estatísticas ----------
@router.callback_query(F.data == "admin:stats")
async def cb_stats(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    users = await db.count_users()
    vips = await db.count_active_vips()
    vip2s = await db.count_active_vip2s()
    total = await db.count_orders()
    delivered = await db.count_orders("delivered")
    pending = await db.count_orders("pending")
    failed = await db.count_orders("failed")
    text = (
        "📊 <b>Estatísticas</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Usuários: <b>{users}</b>\n"
        f"💎 VIPs ativos: <b>{vips}</b>\n"
        f"🔥 VIP2 ativos: <b>{vip2s}</b>\n"
        f"🧾 Pedidos totais: <b>{total}</b>\n"
        f"✅ Entregues: <b>{delivered}</b>\n"
        f"⏳ Pendentes: <b>{pending}</b>\n"
        f"⚠️ Falhas: <b>{failed}</b>"
    )
    await edit_screen(query, text, admin_panel())
    await query.answer()


# ---------- Estoque da API de Passe ----------
@router.callback_query(F.data == "admin:estoque")
async def cb_estoque(query: CallbackQuery, config: Config, passe: PasseApi) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    await query.answer("Consultando estoque...")
    result = await passe.estoque()
    if not result.ok:
        text = "📦 <b>Estoque Passe (API)</b>\n\n⚠️ Não foi possível consultar."
    else:
        d = result.data if isinstance(result.data, dict) else {}
        passes = d.get("passes") or d.get("disponivel") or d.get("estoque") or "?"
        diamonds = d.get("diamonds") or d.get("diamantes") or "?"
        accounts = d.get("contas") or d.get("accounts") or "?"
        text = (
            "📦 <b>Estoque Passe (API)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🎟️ Passes disponíveis: <b>{passes}</b>\n"
            f"💎 Diamantes: <b>{diamonds}</b>\n"
            f"👤 Contas: <b>{accounts}</b>"
        )
    await edit_screen(query, text, admin_panel())


# ---------- Broadcast ----------
@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_start(
    query: CallbackQuery, state: FSMContext, config: Config
) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    await state.set_state(AdminBroadcast.waiting_message)
    await edit_screen(
        query,
        "📢 <b>Broadcast</b>\n\n"
        "Envie a mensagem que será disparada para todos os usuários.\n"
        "Use /cancel para abortar.",
        back_home(),
    )
    await query.answer()


@router.message(AdminBroadcast.waiting_message)
async def broadcast_send(
    message: Message, state: FSMContext, config: Config, db: Database, bot: Bot
) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await state.clear()
    user_ids = await db.all_user_ids()
    await message.answer(f"📤 Enviando para {len(user_ids)} usuários...")

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await message.copy_to(uid)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"✅ Broadcast concluído.\nEnviados: <b>{sent}</b> · Falhas: <b>{failed}</b>",
        reply_markup=admin_panel(),
    )
