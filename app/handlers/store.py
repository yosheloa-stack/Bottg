"""Handler da loja: fluxo de compra com pagamento PIX (Mercado Pago)."""
from __future__ import annotations

import base64
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import texts
from app.catalog import resolve_product
from app.config import Config
from app.database import Database
from app.delivery import DeliveryService
from app.keyboards.inline import (
    back_home,
    cancel_only,
    confirm_purchase,
    payment_pending,
)
from app.services.autolike import AutoLikeApi
from app.services.payments.base import PaymentGateway, PaymentStatus
from app.states import PurchaseFlow
from app.ui import edit_screen
from app.utils import clean_game_id, extract_nick, format_price, is_valid_game_id

logger = logging.getLogger(__name__)

router = Router(name="store")


# ---- 1. Usuário escolhe um produto ----
@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(
    query: CallbackQuery, state: FSMContext, config: Config, db: Database
) -> None:
    code = query.data.split(":", 1)[1]
    rp = await resolve_product(config, db, code)
    if not rp:
        await query.answer("Produto indisponível", show_alert=True)
        return
    if not rp.available:
        await edit_screen(query, texts.OUT_OF_STOCK, back_home())
        await query.answer()
        return

    await state.set_state(PurchaseFlow.waiting_id)
    await state.update_data(product_code=code)
    await edit_screen(
        query,
        texts.PRODUCT_DETAIL.format(
            title=rp.title,
            description=rp.description,
            price=format_price(rp.price),
            stock=rp.stock_label,
        ),
        cancel_only(),
    )
    await query.answer()


# ---- 2. Usuário informa o ID do jogo ----
@router.message(PurchaseFlow.waiting_id)
async def purchase_receive_id(
    message: Message, state: FSMContext, config: Config, db: Database, autolike: AutoLikeApi
) -> None:
    game_id = clean_game_id(message.text or "")
    if not is_valid_game_id(game_id):
        await message.answer(texts.INVALID_ID, reply_markup=cancel_only())
        return

    data = await state.get_data()
    rp = await resolve_product(config, db, data.get("product_code", ""))
    if not rp:
        await state.clear()
        await message.answer(texts.GENERIC_ERROR)
        return

    checking = await message.answer("⏳ Validando ID...")
    info = await autolike.info_player(game_id)
    nick = extract_nick(info.data) if info.ok else None
    nick_line = f"👤 Nick: <b>{nick}</b>\n" if nick else ""

    await state.update_data(game_id=game_id)
    await state.set_state(PurchaseFlow.confirming)
    await checking.edit_text(
        texts.CONFIRM_PURCHASE.format(
            title=rp.title,
            game_id=game_id,
            nick_line=nick_line,
            price=format_price(rp.price),
        ),
        reply_markup=confirm_purchase(rp.code),
    )


# ---- 3. Usuário confirma → gera PIX ----
@router.callback_query(F.data.startswith("confirm:"), PurchaseFlow.confirming)
async def cb_confirm(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
    gateway: PaymentGateway,
) -> None:
    data = await state.get_data()
    rp = await resolve_product(config, db, data.get("product_code", ""))
    game_id = data.get("game_id")
    if not rp or not game_id:
        await state.clear()
        await edit_screen(query, texts.GENERIC_ERROR, back_home())
        await query.answer()
        return

    if not rp.available:
        await state.clear()
        await edit_screen(query, texts.OUT_OF_STOCK, back_home())
        await query.answer()
        return

    await query.answer("Gerando PIX...")
    await query.message.edit_text("⏳ Gerando pagamento PIX...")

    order_id = await db.create_order(
        user_id=query.from_user.id,
        product_code=rp.code,
        game_id=game_id,
        amount=str(rp.price),
    )

    try:
        charge = await gateway.create_pix(
            amount=rp.price,
            description=f"{rp.title} - ID {game_id}",
            external_reference=str(order_id),
            payer_email=f"user{query.from_user.id}@bottg.com",
            payer_name=query.from_user.first_name or "Cliente",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Erro ao gerar PIX para pedido %s", order_id)
        await db.update_order_status(order_id, "failed")
        await query.message.edit_text(texts.GENERIC_ERROR, reply_markup=back_home())
        return

    await db.set_order_payment(order_id, charge.payment_id)
    await state.clear()

    caption = texts.PIX_MESSAGE.format(
        title=rp.title,
        game_id=game_id,
        price=format_price(rp.price),
        qr_code=charge.qr_code,
    )
    kb = payment_pending(charge.payment_id, charge.ticket_url)

    if charge.qr_code_base64:
        try:
            img_bytes = base64.b64decode(charge.qr_code_base64)
            photo = BufferedInputFile(img_bytes, filename="pix.png")
            await query.message.delete()
            await query.message.answer_photo(photo, caption=caption, reply_markup=kb)
            return
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao decodificar QR base64; enviando só o texto")

    await query.message.edit_text(caption, reply_markup=kb)


# ---- 4. Usuário clica "Já paguei / verificar" ----
@router.callback_query(F.data.startswith("check:"))
async def cb_check(
    query: CallbackQuery,
    db: Database,
    gateway: PaymentGateway,
    delivery: DeliveryService,
) -> None:
    payment_id = query.data.split(":", 1)[1]
    order = await db.get_order_by_payment(payment_id)
    if not order:
        await query.answer("Pedido não encontrado.", show_alert=True)
        return

    if order["status"] == "delivered":
        await query.answer("✅ Este pedido já foi entregue!", show_alert=True)
        return

    status = await gateway.get_status(payment_id)
    if status == PaymentStatus.APPROVED:
        await query.answer("✅ Pagamento aprovado! Entregando...", show_alert=True)
        await delivery.fulfill_order(order["id"])
    elif status in (PaymentStatus.PENDING, PaymentStatus.UNKNOWN):
        await query.answer(
            "⏳ Pagamento ainda não identificado. Aguarde alguns segundos e tente de novo.",
            show_alert=True,
        )
    else:
        await query.answer(
            f"❌ Pagamento {status.value}. Gere um novo pedido se necessário.",
            show_alert=True,
        )
