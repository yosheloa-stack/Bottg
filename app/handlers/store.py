"""Handler da loja: fluxo de compra com pagamento PIX (Mercado Pago)."""
from __future__ import annotations

import base64
import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, ForceReply, Message

from app import texts
from app.catalog import (
    price_for_user,
    reseller_discount_percent,
    resolve_product,
    resolve_products,
)
from app.config import Config
from app.database import Database
from app.delivery import DeliveryService
from app.keyboards.inline import (
    autolike_days_menu,
    back_home,
    cancel_only,
    checkout_menu,
    confirm_purchase,
    payment_pending,
    reseller_area_menu,
)
from app.services.autolike import AutoLikeApi
from app.services.passe import PasseApi
from app.services.payments.base import PaymentGateway, PaymentStatus
from app.states import CustomerPlanFlow, PurchaseFlow
from app.ui import edit_screen
from app.utils import clean_game_id, extract_nick, format_price, is_valid_game_id

logger = logging.getLogger(__name__)

router = Router(name="store")


AUTOLIKE_CODES = {"autolike220", "autolike500"}


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _to_cents(value: Decimal) -> int:
    return int((_money(value) * 100).to_integral_value())


def _from_cents(value: int) -> Decimal:
    return (Decimal(int(value)) / Decimal(100)).quantize(Decimal("0.01"))


async def _effective_price(
    db: Database,
    rp,
    user_id: int,
) -> tuple[Decimal, bool, Decimal]:
    price, is_reseller, discount = await price_for_user(db, rp, user_id)
    return _money(price), is_reseller, discount


async def _show_autolike_category(
    query: CallbackQuery,
    db: Database,
    config: Config,
    code: str,
) -> None:
    rp = await resolve_product(config, db, code)
    if not rp:
        await query.answer("Produto não encontrado.", show_alert=True)
        return

    unit_price, is_reseller, discount = await _effective_price(
        db, rp, query.from_user.id
    )

    if not rp.service_ready:
        await edit_screen(
            query,
            "⚠️ <b>Serviço temporariamente indisponível.</b>\n\n"
            "A integração necessária ainda não está configurada.",
            back_home(),
        )
        await query.answer()
        return

    if unit_price <= 0:
        await edit_screen(
            query,
            "⚠️ <b>Preço ainda não configurado.</b>\n\n"
            "Esse serviço será liberado em breve.",
            back_home(),
        )
        await query.answer()
        return

    if rp.stock == 0:
        await edit_screen(query, texts.OUT_OF_STOCK, back_home())
        await query.answer()
        return

    if code == "autolike220":
        title = "❤️ <b>AUTO LIKE 220</b>"
        rate = "<b>220 likes por dia</b>"
        provider_text = "Envio diário automático pela Auto System."
    else:
        title = "💎 <b>AUTO LIKE 500</b>"
        rate = "<b>aproximadamente 500 a 1.000 likes por dia</b>"
        provider_text = "Envio diário automático pela FFHub."

    reseller_line = (
        f"\n🤝 Revendedor: <b>{discount}% de desconto aplicado</b>"
        if is_reseller and discount > 0
        else ""
    )

    await edit_screen(
        query,
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Você recebe {rate} durante o período escolhido.\n"
        f"{provider_text}\n\n"
        "📆 Escolha entre <b>1 e 90 dias</b>.\n"
        "Você pode usar os atalhos abaixo ou escolher qualquer número, "
        "como 33, 46 ou 73 dias.\n\n"
        f"💰 Valor por dia: <b>R$ {format_price(unit_price)}</b>"
        f"{reseller_line}\n\n"
        "O total é calculado automaticamente antes de gerar o PIX.",
        autolike_days_menu(code),
    )
    await query.answer()


async def _show_dynamic_confirmation(
    target: Message | CallbackQuery,
    db: Database,
    config: Config,
    user_id: int,
    code: str,
    days: int,
) -> None:
    rp = await resolve_product(config, db, code)
    if not rp:
        return
    unit_price, is_reseller, discount = await _effective_price(db, rp, user_id)
    total = _money(unit_price * Decimal(days))
    balance = _from_cents(await db.get_balance_cents(user_id))

    if code == "autolike220":
        daily = "220 likes/dia"
    else:
        daily = "aprox. 500–1.000 likes/dia"

    text = (
        "🧾 <b>CONFIRMAR PLANO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Serviço: <b>{rp.title}</b>\n"
        f"❤️ Entrega: <b>{daily}</b>\n"
        f"📆 Período: <b>{days} dias</b>\n"
        f"💵 Valor/dia: <b>R$ {format_price(unit_price)}</b>\n"
        f"💰 Total: <b>R$ {format_price(total)}</b>\n"
        f"👛 Seu saldo: <b>R$ {format_price(balance)}</b>"
    )
    if is_reseller and discount > 0:
        text += f"\n🤝 Desconto revenda: <b>{discount}%</b>"
    text += "\n\nEscolha como deseja pagar:"

    kb = checkout_menu(code, ref_value=str(days), allow_balance=True)
    if isinstance(target, CallbackQuery):
        await edit_screen(target, text, kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


async def _create_pix_order(
    query: CallbackQuery,
    db: Database,
    gateway: PaymentGateway,
    *,
    product_code: str,
    amount: Decimal,
    description: str,
    game_id: str = "",
    plan_days: int = 0,
) -> None:
    if not getattr(gateway, "configured", True):
        await query.answer("Pagamento temporariamente indisponível.", show_alert=True)
        await edit_screen(
            query,
            "⚠️ <b>Mercado Pago ainda não está configurado.</b>\n\n"
            "Tente novamente mais tarde.",
            back_home(),
        )
        return

    await query.answer("Gerando PIX...")
    try:
        await query.message.edit_text("⏳ Gerando pagamento PIX...")
    except Exception:
        pass

    order_id = await db.create_order(
        user_id=query.from_user.id,
        product_code=product_code,
        game_id=game_id,
        amount=str(_money(amount)),
        plan_days=plan_days,
        paid_with="pix",
    )

    try:
        charge = await gateway.create_pix(
            amount=_money(amount),
            description=description,
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

    caption = (
        "💠 <b>PIX GERADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 {description}\n"
        f"💰 Valor: <b>R$ {format_price(amount)}</b>\n\n"
        "1️⃣ Copie o código PIX abaixo\n"
        "2️⃣ Pague no aplicativo do seu banco\n"
        "3️⃣ A confirmação é automática\n\n"
        f"<code>{charge.qr_code}</code>\n\n"
        "⏳ <i>Aguardando pagamento...</i>"
    )
    kb = payment_pending(charge.payment_id, charge.ticket_url)

    if charge.qr_code_base64:
        try:
            img_bytes = base64.b64decode(charge.qr_code_base64)
            photo = BufferedInputFile(img_bytes, filename="pix.png")
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.answer_photo(photo, caption=caption, reply_markup=kb)
            return
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao decodificar QR base64; enviando somente texto")

    await query.message.edit_text(caption, reply_markup=kb)


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

    await state.update_data(product_code=code)

    if rp.product.delivery in {"ffhub_autolike", "ffhub_like_once"}:
        await state.set_state(PurchaseFlow.confirming)
        if rp.product.delivery == "ffhub_like_once":
            text = texts.LIKE2_SINGLE_CONFIRM.format(
                price=format_price(rp.price),
            )
        else:
            text = texts.PREMIUM_AUTOLIKE_CONFIRM.format(
                title=rp.title,
                days=rp.product.days,
                price=format_price(rp.price),
            )
        await edit_screen(
            query,
            text,
            confirm_purchase(rp.code),
        )
        await query.answer()
        return

    await state.set_state(PurchaseFlow.waiting_id)
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
    message: Message,
    state: FSMContext,
    config: Config,
    db: Database,
    autolike: AutoLikeApi,
    passe: PasseApi,
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
    if rp.delivery == "passe":
        info = await passe.confirmar(game_id, region=config.default_region)
    else:
        info = await autolike.info_player(game_id)

    if not info.ok:
        data = info.data if isinstance(info.data, dict) else {}
        api_message = data.get("mensagem") or data.get("message")
        await checking.edit_text(
            f"❌ {api_message or 'Não foi possível validar esse ID. Confira os números e tente novamente.'}",
            reply_markup=cancel_only(),
        )
        return

    nick = extract_nick(info.data) if info.ok else None
    if rp.delivery == "passe":
        nick = info.data.get("nickname") or info.data.get("nick") or nick
        level = info.data.get("nivel") or info.data.get("level")
        account_region = info.data.get("regiao") or config.default_region
        nick_line = (
            f"👤 Nick: <b>{nick or 'Não informado'}</b>\n"
            f"🏅 Nível: <b>{level or '—'}</b>\n"
            f"🌎 Região: <b>{account_region}</b>\n"
        )
    else:
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
    game_id = data.get("game_id") or ""
    if not rp or (
        rp.product.delivery not in {"ffhub_autolike", "ffhub_like_once"}
        and not game_id
    ):
        await state.clear()
        await edit_screen(query, texts.GENERIC_ERROR, back_home())
        await query.answer()
        return

    if not rp.available:
        await state.clear()
        await edit_screen(query, texts.OUT_OF_STOCK, back_home())
        await query.answer()
        return

    if not getattr(gateway, "configured", True):
        await query.answer("Pagamento temporariamente indisponível.", show_alert=True)
        await edit_screen(
            query,
            "⚠️ <b>Mercado Pago ainda não está configurado.</b>\n\n"
            "Avise um administrador.",
            back_home(),
        )
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
            description=(
            f"{rp.title} - ID {game_id}"
            if game_id
            else f"{rp.title}"
        ),
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

    if rp.product.delivery == "ffhub_like_once":
        caption = texts.LIKE2_SINGLE_PIX.format(
            price=format_price(rp.price),
            qr_code=charge.qr_code,
        )
    elif rp.product.delivery == "ffhub_autolike":
        caption = texts.PREMIUM_AUTOLIKE_PIX.format(
            title=rp.title,
            days=rp.product.days,
            price=format_price(rp.price),
            qr_code=charge.qr_code,
        )
    else:
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
