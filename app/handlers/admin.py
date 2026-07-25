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
from aiogram.types import CallbackQuery, Message

from app.catalog import resolve_product, resolve_products
from app.config import Config
from app.database import Database
from app.keyboards.inline import (
    admin_panel,
    admin_product_edit,
    admin_products,
    back_home,
)
from app.services.passe import PasseApi
from app.states import AdminBroadcast, AdminPrice, AdminStock
from app.ui import edit_screen
from app.utils import format_price

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


async def _deny(query: CallbackQuery) -> None:
    await query.answer("Sem permissão.", show_alert=True)


# ---------- Entrada ----------
@router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await message.answer("⚙️ <b>Painel Admin</b>", reply_markup=admin_panel())


@router.callback_query(F.data == "admin:panel")
async def cb_panel(query: CallbackQuery, config: Config) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    await edit_screen(query, "⚙️ <b>Painel Admin</b>", admin_panel())
    await query.answer()


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
async def cb_price_start(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    code = query.data.split(":", 1)[1]
    await state.set_state(AdminPrice.waiting_value)
    await state.update_data(code=code)
    await edit_screen(
        query,
        "💰 <b>Novo preço</b>\n\n"
        "Envie o novo valor em reais (ex.: <code>24.90</code>).\n"
        "Use /cancel para abortar.",
        back_home(),
    )
    await query.answer()


@router.message(AdminPrice.waiting_value)
async def price_receive(
    message: Message, state: FSMContext, config: Config, db: Database
) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    raw = (message.text or "").strip().replace(",", ".").replace("R$", "").strip()
    try:
        value = Decimal(raw)
        if value <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Valor inválido. Envie algo como <code>24.90</code>.")
        return
    data = await state.get_data()
    code = data.get("code", "")
    await db.set_price(code, f"{value:.2f}")
    await state.clear()
    rp = await resolve_product(config, db, code)
    await message.answer(
        f"✅ Preço de <b>{rp.title if rp else code}</b> atualizado para "
        f"<b>R$ {format_price(value)}</b>.",
        reply_markup=admin_product_edit(code),
    )


# ---- Alterar estoque ----
@router.callback_query(F.data.startswith("admstock:"))
async def cb_stock_start(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    code = query.data.split(":", 1)[1]
    await state.set_state(AdminStock.waiting_value)
    await state.update_data(code=code)
    await edit_screen(
        query,
        "📦 <b>Novo estoque</b>\n\n"
        "Envie a quantidade em número inteiro (ex.: <code>50</code>).\n"
        "Use <code>-1</code> para estoque ilimitado.\n"
        "Use /cancel para abortar.",
        back_home(),
    )
    await query.answer()


@router.message(AdminStock.waiting_value)
async def stock_receive(
    message: Message, state: FSMContext, config: Config, db: Database
) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    raw = (message.text or "").strip()
    try:
        value = int(raw)
    except ValueError:
        await message.answer("⚠️ Valor inválido. Envie um número inteiro (ex.: <code>50</code>).")
        return
    if value < -1:
        value = -1
    data = await state.get_data()
    code = data.get("code", "")
    await db.set_stock(code, value)
    await state.clear()
    rp = await resolve_product(config, db, code)
    await message.answer(
        f"✅ Estoque de <b>{rp.title if rp else code}</b> atualizado para "
        f"<b>{'Ilimitado' if value < 0 else value}</b>.",
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
    total = await db.count_orders()
    delivered = await db.count_orders("delivered")
    pending = await db.count_orders("pending")
    failed = await db.count_orders("failed")
    text = (
        "📊 <b>Estatísticas</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Usuários: <b>{users}</b>\n"
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
