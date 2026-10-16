"""Handlers comuns: /start, menu, navegação, cancelar, suporte, pedidos."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home, main_menu
from app.ui import edit_screen, send_menu
from app.utils import format_price

router = Router(name="common")


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    await state.clear()

    # Deep link de aluguel: https://t.me/BOT?startgroup=rent_ID
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if message.chat.type in {"group", "supergroup"}:
        if payload.startswith("rent_") and message.from_user:
            try:
                rental_id = int(payload.split("_", 1)[1])
            except (ValueError, IndexError):
                rental_id = 0

            if rental_id and await db.bind_bot_rental(
                rental_id,
                message.from_user.id,
                message.chat.id,
            ):
                rental = await db.get_bot_rental(rental_id)
                expires_text = "—"
                if rental:
                    expires_text = datetime.fromtimestamp(
                        int(rental["expires_at"]),
                        ZoneInfo("America/Sao_Paulo"),
                    ).strftime("%d/%m/%Y às %H:%M")
                await message.answer(
                    "🤖 <b>AURORA SYSTEM ATIVADO NESTE GRUPO</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📄 Licença: <code>#{rental_id}</code>\n"
                    f"📆 Válida até: <b>{expires_text}</b>\n\n"
                    "✅ O aluguel foi vinculado a este grupo."
                )
            else:
                await message.answer(
                    "⚠️ Não consegui vincular essa licença a este grupo.\n"
                    "Confirme que você está usando a conta que comprou o aluguel."
                )
        return

    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await send_menu(
        message,
        config.menu_banner,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(False),
    )


@router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    state: FSMContext,
    config: Config,
    bot: Bot,
) -> None:
    await state.clear()
    if message.chat.type != "private":
        await message.reply(
            "ℹ️ O menu de compras funciona no privado do bot."
        )
        return
    await send_menu(
        message,
        config.menu_banner,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(False),
    )


@router.callback_query(F.data == "menu:home")
async def cb_home(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await edit_screen(
        query,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(False),
    )
    await query.answer()


@router.callback_query(F.data == "menu:store")
async def cb_store(query: CallbackQuery, config: Config, db: Database) -> None:
    # Compatibilidade com mensagens antigas: volta para o menu novo.
    await edit_screen(
        query,
        texts.WELCOME.format(shop_name=config.shop_name),
        main_menu(False),
    )
    await query.answer()


@router.callback_query(F.data == "menu:autolike")
async def cb_autolike_store(
    query: CallbackQuery,
    config: Config,
    db: Database,
) -> None:
    # Compatibilidade com o botão antigo do Auto Like 500/1000.
    await edit_screen(
        query,
        "💎 <b>Auto Like</b>\n\n"
        "Agora existem duas modalidades. Escolha no menu principal:",
        main_menu(False),
    )
    await query.answer()


@router.callback_query(F.data == "noop:unavailable")
async def cb_unavailable(query: CallbackQuery) -> None:
    await query.answer(
        "Este plano ainda não está disponível para compra.",
        show_alert=True,
    )


@router.callback_query(F.data == "menu:like_info")
async def cb_like_info(query: CallbackQuery) -> None:
    await edit_screen(query, texts.LIKE_PRIVATE_BLOCKED, back_home())
    await query.answer()


@router.callback_query(F.data == "menu:my_id")
async def cb_my_id(query: CallbackQuery) -> None:
    await edit_screen(
        query,
        "🆔 <b>Seu ID do Telegram</b>\n\n"
        f"<code>{query.from_user.id}</code>\n\n"
        "Toque e segure no número para copiar.",
        back_home(),
    )
    await query.answer()


@router.message(Command("ativarbot"))
async def cmd_activate_rental(
    message: Message,
    db: Database,
) -> None:
    if message.chat.type not in {"group", "supergroup"} or not message.from_user:
        await message.reply(
            "Use este comando dentro do grupo que vai receber o bot."
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply(
            "Use: <code>/ativarbot ID_DA_LICENÇA</code>"
        )
        return
    try:
        rental_id = int(parts[1].strip().lstrip("#"))
    except ValueError:
        await message.reply("⚠️ ID de licença inválido.")
        return

    ok = await db.bind_bot_rental(
        rental_id,
        message.from_user.id,
        message.chat.id,
    )
    if not ok:
        await message.reply(
            "⚠️ Essa licença não está disponível para sua conta ou já expirou."
        )
        return

    await message.reply(
        "✅ <b>ALUGUEL ATIVADO NESTE GRUPO</b>\n"
        f"📄 Licença: <code>#{rental_id}</code>"
    )


@router.message(Command("id", "meuid"))
async def cmd_my_id(message: Message) -> None:
    await message.reply(
        "🆔 <b>Seu ID do Telegram</b>\n\n"
        f"<code>{message.from_user.id}</code>"
    )


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
        main_menu(False),
    )
    await query.answer("Cancelado")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED)
