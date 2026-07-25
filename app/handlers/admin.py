"""Painel administrativo: estatísticas, estoque e broadcast."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Config
from app.database import Database
from app.keyboards.inline import admin_panel, back_home
from app.services.passe import PasseApi
from app.states import AdminBroadcast

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await message.answer("⚙️ <b>Painel Admin</b>", reply_markup=admin_panel())


@router.callback_query(F.data == "admin:panel")
async def cb_panel(query: CallbackQuery, config: Config) -> None:
    if not _is_admin(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    await query.message.edit_text("⚙️ <b>Painel Admin</b>", reply_markup=admin_panel())
    await query.answer()


@router.callback_query(F.data == "admin:stats")
async def cb_stats(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _is_admin(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    users = await db.count_users()
    total = await db.count_orders()
    delivered = await db.count_orders("delivered")
    pending = await db.count_orders("pending")
    failed = await db.count_orders("failed")
    text = (
        "📊 <b>Estatísticas</b>\n\n"
        f"👥 Usuários: <b>{users}</b>\n"
        f"🧾 Pedidos totais: <b>{total}</b>\n"
        f"✅ Entregues: <b>{delivered}</b>\n"
        f"⏳ Pendentes: <b>{pending}</b>\n"
        f"⚠️ Falhas: <b>{failed}</b>"
    )
    await query.message.edit_text(text, reply_markup=admin_panel())
    await query.answer()


@router.callback_query(F.data == "admin:estoque")
async def cb_estoque(query: CallbackQuery, config: Config, passe: PasseApi) -> None:
    if not _is_admin(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    await query.answer("Consultando estoque...")
    result = await passe.estoque()
    if not result.ok:
        text = "📦 <b>Estoque Passe</b>\n\n⚠️ Não foi possível consultar o estoque."
    else:
        d = result.data if isinstance(result.data, dict) else {}
        passes = d.get("passes") or d.get("disponivel") or d.get("estoque") or "?"
        diamonds = d.get("diamonds") or d.get("diamantes") or "?"
        accounts = d.get("contas") or d.get("accounts") or "?"
        text = (
            "📦 <b>Estoque Passe</b>\n\n"
            f"🎟️ Passes disponíveis: <b>{passes}</b>\n"
            f"💎 Diamantes: <b>{diamonds}</b>\n"
            f"👤 Contas: <b>{accounts}</b>"
        )
    await query.message.edit_text(text, reply_markup=admin_panel())


@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_start(
    query: CallbackQuery, state: FSMContext, config: Config
) -> None:
    if not _is_admin(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    await state.set_state(AdminBroadcast.waiting_message)
    await query.message.edit_text(
        "📢 <b>Broadcast</b>\n\nEnvie a mensagem que será disparada para todos os usuários.\n"
        "Use /cancel para abortar.",
        reply_markup=back_home(),
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
        await asyncio.sleep(0.05)  # respeita limites do Telegram

    await message.answer(
        f"✅ Broadcast concluído.\nEnviados: <b>{sent}</b> · Falhas: <b>{failed}</b>",
        reply_markup=admin_panel(),
    )
