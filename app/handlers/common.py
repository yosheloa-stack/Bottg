"""Handlers comuns: /start, menu, navegação, cancelar, suporte, pedidos."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.catalog import resolve_products
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home, main_menu, store_menu
from app.ui import edit_screen, send_menu
from app.utils import format_price

router = Router(name="common")


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
    # Em grupos, /start não abre o menu (o menu é experiência de privado)
    if message.chat.type != "private":
        await message.reply(
            "👋 Use <code>/like SEU_ID</code> para enviar likes.\n"
            "Para comprar Passe e Auto-Like, me chame no privado."
        )
        return
    await send_menu(
        message,
        config.menu_banner,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(_is_admin(message.from_user.id, config)),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    if message.chat.type != "private":
        return
    await send_menu(
        message,
        config.menu_banner,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(_is_admin(message.from_user.id, config)),
    )


@router.callback_query(F.data == "menu:home")
async def cb_home(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await edit_screen(
        query,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(_is_admin(query.from_user.id, config)),
    )
    await query.answer()


@router.callback_query(F.data == "menu:store")
async def cb_store(query: CallbackQuery, config: Config, db: Database) -> None:
    products = await resolve_products(config, db)
    await edit_screen(query, texts.STORE_HEADER, store_menu(products))
    await query.answer()


@router.callback_query(F.data == "menu:like_info")
async def cb_like_info(query: CallbackQuery) -> None:
    await edit_screen(query, texts.LIKE_PRIVATE_BLOCKED, back_home())
    await query.answer()


@router.callback_query(F.data == "menu:support")
async def cb_support(query: CallbackQuery, config: Config) -> None:
    await edit_screen(
        query,
        "🆘 <b>Suporte</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Precisa de ajuda com um pedido ou pagamento?\n"
        f"Fale com nossa equipe: {config.support_username}\n\n"
        "Tenha em mãos o número do seu pedido (#).",
        back_home(),
    )
    await query.answer()


@router.callback_query(F.data == "menu:orders")
async def cb_orders(query: CallbackQuery, db: Database, config: Config) -> None:
    orders = await db.recent_orders(limit=100)
    mine = [o for o in orders if o["user_id"] == query.from_user.id][:10]
    if not mine:
        text = "📦 <b>Meus pedidos</b>\n\nVocê ainda não tem pedidos."
    else:
        lines = ["📦 <b>Seus últimos pedidos</b>\n━━━━━━━━━━━━━━━━━━━━"]
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
                f"    ID: <code>{o['game_id']}</code> · "
                f"R$ {format_price(float(o['amount']))} · {o['status']}"
            )
        text = "\n".join(lines)
    await edit_screen(query, text, back_home())
    await query.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await edit_screen(
        query,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(_is_admin(query.from_user.id, config)),
    )
    await query.answer("Cancelado")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED)
