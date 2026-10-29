"""Handlers comuns: /start, menu, navegação, cancelar, suporte, pedidos."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app import texts
from app.config import Config
from app.database import Database
from app.i18n import t, user_lang, welcome_text
from app.keyboards.inline import back_home, cancel_only, language_menu, main_menu
from app.states import PurchaseFlow
from app.ui import edit_screen, send_menu
from app.utils import format_price

router = Router(name="common")


async def _menu_media(db: Database, config: Config) -> str:
    override = (await db.get_owner_setting("MENU_MEDIA") or "").strip()
    return override or config.menu_banner


async def _customer_welcome(db: Database, config: Config, user_id: int, lang: str) -> str:
    return await welcome_text(db, config.shop_name, user_id, lang)


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
    lang = await user_lang(db, message.from_user)
    await send_menu(
        message,
        await _menu_media(db, config),
        await _customer_welcome(db, config, message.from_user.id, lang),
        main_menu(False, lang),
    )


@router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    state: FSMContext,
    config: Config,
    bot: Bot,
    db: Database,
) -> None:
    await state.clear()
    lang = await user_lang(db, message.from_user)
    if message.chat.type != "private":
        await message.reply(t(lang, "menu_private_only"))
        return
    await send_menu(
        message,
        await _menu_media(db, config),
        await _customer_welcome(db, config, message.from_user.id, lang),
        main_menu(False, lang),
    )


async def _open_customer_home(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    await state.clear()
    await db.clear_customer_pending_input(query.from_user.id)
    lang = await user_lang(db, query.from_user)
    await edit_screen(
        query,
        await _customer_welcome(db, config, query.from_user.id, lang),
        main_menu(False, lang),
    )


@router.callback_query(F.data == "menu:lang")
async def cb_language(query: CallbackQuery, db: Database) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    lang = await user_lang(db, query.from_user)
    await edit_screen(query, t(lang, "language_title"), language_menu(lang))


@router.callback_query(F.data.regexp(r"^lang:(pt|es|en)$"))
async def cb_set_language(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    lang = query.data.split(":", 1)[1]
    await db.set_user_language(query.from_user.id, lang)
    try:
        await query.answer(t(lang, "language_saved"))
    except Exception:
        pass
    await _open_customer_home(query, state, config, db)


@router.message(Command("idioma", "language", "lang"))
async def cmd_language(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user:
        return
    lang = await user_lang(db, message.from_user)
    if message.chat.type != "private":
        await message.reply(t(lang, "menu_private_only"))
        return
    await state.clear()
    await message.answer(t(lang, "language_title"), reply_markup=language_menu(lang))


@router.callback_query(F.data == "shop:giftcard")
async def cb_giftcard(
    query: CallbackQuery,
    db: Database,
) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    await db.set_customer_pending_input(
        query.from_user.id,
        "gift_card",
        "",
        ttl_seconds=600,
    )
    await query.message.answer(
        "🎁 <b>RESGATAR GIFT CARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Responda esta mensagem com o código do Gift Card.\n\n"
        "Exemplo: <code>AURORA-ABCD-1234</code>\n"
        "\n🔧 REF: GIFT",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Digite o código",
        ),
    )


async def _redeem_gift(
    message: Message,
    db: Database,
    code: str,
) -> None:
    ok, reason, amount_cents, balance_cents = await db.redeem_gift_card(
        code,
        message.from_user.id,
    )
    await db.clear_customer_pending_input(message.from_user.id)
    if not ok:
        await message.reply(f"⚠️ {reason}")
        return

    await message.reply(
        "🎁 <b>GIFT CARD RESGATADO!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Crédito recebido: <b>R$ {format_price(amount_cents / 100)}</b>\n"
        f"👛 Saldo atual: <b>R$ {format_price(balance_cents / 100)}</b>\n\n"
        "✅ O valor já pode ser usado nas compras do bot.",
        reply_markup=back_home(),
    )


@router.message(F.reply_to_message.text.regexp(r"REF:\s*GIFT"))
async def giftcard_reply(
    message: Message,
    db: Database,
) -> None:
    if not message.from_user:
        return
    code = (message.text or "").strip()
    if not code:
        await message.reply("⚠️ Envie o código do Gift Card.")
        return
    await _redeem_gift(message, db, code)


@router.message(Command("resgatar"))
async def cmd_redeem_giftcard(
    message: Message,
    db: Database,
) -> None:
    if not message.from_user:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply(
            "Use: <code>/resgatar CODIGO</code>"
        )
        return
    await _redeem_gift(message, db, parts[1].strip())


@router.callback_query(
    F.data.regexp(r"^(?:nav:v2:passe|shop:passe)$")
)
async def cb_passe_entry(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    # Responde o callback primeiro para nunca deixar o botão carregando.
    try:
        await query.answer()
    except Exception:
        pass

    try:
        await db.clear_customer_pending_input(query.from_user.id)
        await state.clear()

        product = config.products.get("passe")
        if not product:
            await query.message.answer(
                "⚠️ Passe de Elite indisponível no momento.",
                reply_markup=back_home(),
            )
            return

        private_key = await db.get_owner_setting("PASSE_API_KEY")
        if not (private_key or config.passe_api_key):
            await edit_screen(
                query,
                "⚠️ <b>Passe de Elite temporariamente indisponível.</b>\n\n"
                "A integração de entrega ainda não está configurada.",
                back_home(),
            )
            return

        await state.set_state(PurchaseFlow.waiting_id)
        await state.update_data(product_code="passe")
        await edit_screen(
            query,
            "🎟️ <b>PASSE DE ELITE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Envie agora o <b>UID</b> da conta que vai receber o passe.\n\n"
            "Depois de validar o UID, o bot mostra o valor e as opções "
            "de pagamento por PIX ou saldo.",
            cancel_only(),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Falha ao abrir Passe de Elite user=%s",
            query.from_user.id,
        )
        try:
            await query.message.answer(
                "⚠️ Não consegui abrir o Passe de Elite agora. "
                "Tente novamente pelo /start.",
                reply_markup=back_home(),
            )
        except Exception:
            pass


@router.callback_query(F.data == "nav:v2:home")
async def cb_home_v2(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    await _open_customer_home(query, state, config, db)


@router.callback_query(F.data == "menu:home")
async def cb_home_legacy(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    # Só para mensagens antigas que ainda estejam no histórico.
    await _open_customer_home(query, state, config, db)


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


@router.callback_query(F.data == "menu:orders")
async def cb_orders(query: CallbackQuery, db: Database, config: Config) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    orders = await db.recent_orders(limit=100)
    mine = [o for o in orders if o["user_id"] == query.from_user.id][:10]
    if not mine:
        text = "📦 <b>Meus pedidos</b>\n\nVocê ainda não tem pedidos."
    else:
        lines = ["📦 <b>Seus últimos pedidos</b>\n━━━━━━━━━━━━━━━━━━━━"]
        status_emoji = {
            "pending": "⏳",
            "paid": "💳",
            "awaiting_id": "🆔",
            "active": "🟢",
            "delivered": "✅",
            "failed": "⚠️",
            "expired": "❌",
        }
        for o in mine:
            product = config.products.get(o["product_code"])
            title = product.title if product else o["product_code"]
            emoji = status_emoji.get(o["status"], "•")
            uid = str(o.get("game_id") or "").strip()
            uid_text = f"<code>{uid}</code>" if uid else "aguardando"
            days = int(o.get("plan_days") or 0)
            days_text = f" · {days} dias" if days > 0 else ""
            payment_text = "saldo" if o.get("paid_with") == "balance" else "PIX"
            lines.append(
                f"{emoji} #{o['id']} — {title}\n"
                f"    UID: {uid_text}{days_text}\n"
                f"    R$ {format_price(float(o['amount']))} · "
                f"{payment_text} · {o['status']}"
            )
        text = "\n".join(lines)
    await edit_screen(query, text, back_home())


@router.callback_query(F.data == "cancel")
async def cb_cancel(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    await state.clear()
    lang = await user_lang(db, query.from_user)
    await edit_screen(
        query,
        await _customer_welcome(db, config, query.from_user.id, lang),
        main_menu(False, lang),
    )
    await query.answer(t(lang, "cancelled"))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED)
