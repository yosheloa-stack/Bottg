"""Handlers comuns: /start, menu, navegação, cancelar, suporte, pedidos."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home, main_menu, store_menu
from app.utils import format_price

router = Router(name="common")

SHOP_NAME = "Loja FF"


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    await state.clear()
    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await message.answer(
        texts.WELCOME.format(shop_name=SHOP_NAME),
        reply_markup=main_menu(config, _is_admin(message.from_user.id, config)),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    await message.answer(
        texts.MENU_HINT,
        reply_markup=main_menu(config, _is_admin(message.from_user.id, config)),
    )


@router.callback_query(F.data == "menu:home")
async def cb_home(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await query.message.edit_text(
        texts.WELCOME.format(shop_name=SHOP_NAME),
        reply_markup=main_menu(config, _is_admin(query.from_user.id, config)),
    )
    await query.answer()


@router.callback_query(F.data == "menu:store")
async def cb_store(query: CallbackQuery, config: Config) -> None:
    await query.message.edit_text(
        "🛒 <b>Loja</b>\n\nEscolha um produto para comprar:",
        reply_markup=store_menu(config),
    )
    await query.answer()


@router.callback_query(F.data == "menu:support")
async def cb_support(query: CallbackQuery) -> None:
    await query.message.edit_text(
        "🆘 <b>Suporte</b>\n\n"
        "Precisa de ajuda com um pedido ou pagamento?\n"
        "Fale com nossa equipe: @seu_suporte\n\n"
        "Tenha em mãos o número do seu pedido (#).",
        reply_markup=back_home(),
    )
    await query.answer()


@router.callback_query(F.data == "menu:orders")
async def cb_orders(query: CallbackQuery, db: Database, config: Config) -> None:
    orders = await db.recent_orders(limit=50)
    mine = [o for o in orders if o["user_id"] == query.from_user.id][:10]
    if not mine:
        text = "📦 <b>Meus pedidos</b>\n\nVocê ainda não tem pedidos."
    else:
        lines = ["📦 <b>Seus últimos pedidos</b>\n"]
        status_emoji = {
            "pending": "⏳",
            "paid": "💳",
            "delivered": "✅",
            "failed": "⚠️",
            "expired": "❌",
        }
        for o in mine:
            product = config.products.get(o["product_code"])
            title = product.title if product else o["product_code"]
            emoji = status_emoji.get(o["status"], "•")
            lines.append(
                f"{emoji} #{o['id']} — {title}\n"
                f"    ID: <code>{o['game_id']}</code> · R$ {format_price(float(o['amount']))} · {o['status']}"
            )
        text = "\n".join(lines)
    await query.message.edit_text(text, reply_markup=back_home())
    await query.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await query.message.edit_text(
        texts.MENU_HINT,
        reply_markup=main_menu(config, _is_admin(query.from_user.id, config)),
    )
    await query.answer("Cancelado")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED)
