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

    # Fallback quando o Mercado Pago não devolver imagem base64.
    # O menu principal normalmente é uma animação, então não dá para usar
    # edit_text diretamente nela.
    msg = query.message
    if getattr(msg, "animation", None) or getattr(msg, "photo", None) \
            or getattr(msg, "document", None) or getattr(msg, "video", None):
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(caption, reply_markup=kb)
    else:
        await msg.edit_text(caption, reply_markup=kb)


# ---------- Novo menu cliente ----------
@router.callback_query(F.data == "shop:auto220")
async def shop_auto220(query: CallbackQuery, db: Database, config: Config) -> None:
    await _show_autolike_category(query, db, config, "autolike220")


@router.callback_query(F.data == "shop:auto500")
async def shop_auto500(query: CallbackQuery, db: Database, config: Config) -> None:
    await _show_autolike_category(query, db, config, "autolike500")


@router.callback_query(F.data.startswith("days:"))
async def choose_preset_days(
    query: CallbackQuery,
    db: Database,
    config: Config,
) -> None:
    parts = query.data.split(":")
    if len(parts) != 3:
        return await query.answer("Opção inválida.", show_alert=True)
    code = parts[1]
    try:
        days = int(parts[2])
    except ValueError:
        return await query.answer("Quantidade de dias inválida.", show_alert=True)
    if code not in AUTOLIKE_CODES or not 1 <= days <= 90:
        return await query.answer("Escolha entre 1 e 90 dias.", show_alert=True)
    await _show_dynamic_confirmation(
        query, db, config, query.from_user.id, code, days
    )


@router.callback_query(F.data.startswith("dayscustom:"))
async def choose_custom_days(
    query: CallbackQuery,
    state: FSMContext,
) -> None:
    code = query.data.split(":", 1)[1]
    if code not in AUTOLIKE_CODES:
        return await query.answer("Produto inválido.", show_alert=True)

    await state.set_state(CustomerPlanFlow.waiting_days)
    await state.update_data(custom_plan_code=code)
    await query.answer()
    await query.message.answer(
        "📆 <b>ESCOLHER PERÍODO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Digite quantos dias você quer contratar.\n"
        "Pode ser qualquer número entre <b>1 e 90</b>, por exemplo: "
        "<code>33</code> ou <code>46</code>.\n"
        f"\n🔧 REF: DAYS:{code}",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Ex.: 33",
        ),
    )


@router.message(
    F.reply_to_message.text.regexp(r"REF:\s*DAYS:(autolike220|autolike500)")
)
async def custom_days_reply(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    import re

    replied = message.reply_to_message.text or ""
    match = re.search(r"REF:\s*DAYS:(autolike220|autolike500)", replied)
    if not match:
        return

    try:
        days = int((message.text or "").strip())
    except ValueError:
        await message.reply("⚠️ Envie somente um número de <b>1 a 90</b>.")
        return
    if not 1 <= days <= 90:
        await message.reply("⚠️ O período permitido é de <b>1 a 90 dias</b>.")
        return

    await state.clear()
    await _show_dynamic_confirmation(
        message, db, config, message.from_user.id, match.group(1), days
    )


@router.message(CustomerPlanFlow.waiting_days, F.text.regexp(r"^(?!/).+"))
async def custom_days_state(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    data = await state.get_data()
    code = str(data.get("custom_plan_code") or "")
    if code not in AUTOLIKE_CODES:
        await state.clear()
        return
    try:
        days = int((message.text or "").strip())
    except ValueError:
        await message.reply("⚠️ Envie somente um número de <b>1 a 90</b>.")
        return
    if not 1 <= days <= 90:
        await message.reply("⚠️ O período permitido é de <b>1 a 90 dias</b>.")
        return

    await state.clear()
    await _show_dynamic_confirmation(
        message, db, config, message.from_user.id, code, days
    )


@router.callback_query(F.data == "shop:passe")
async def shop_passe(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    rp = await resolve_product(config, db, "passe")
    if not rp:
        return await query.answer("Produto indisponível.", show_alert=True)
    price, is_reseller, discount = await _effective_price(
        db, rp, query.from_user.id
    )
    if price <= 0 or rp.stock == 0:
        await edit_screen(query, texts.OUT_OF_STOCK, back_home())
        await query.answer()
        return

    await state.set_state(PurchaseFlow.waiting_id)
    await state.update_data(product_code="passe")
    reseller_line = (
        f"\n🤝 Desconto revenda: <b>{discount}%</b>"
        if is_reseller and discount > 0
        else ""
    )
    await edit_screen(
        query,
        "🎟️ <b>PASSE DE ELITE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Envie o <b>UID</b> da conta que vai receber o passe.\n\n"
        f"💰 Valor: <b>R$ {format_price(price)}</b>"
        f"{reseller_line}\n\n"
        "Depois de validar o UID, você escolhe PIX ou saldo.",
        cancel_only(),
    )
    await query.answer()


@router.callback_query(F.data == "shop:reseller")
async def shop_reseller(
    query: CallbackQuery,
    db: Database,
    config: Config,
) -> None:
    if await db.is_reseller(query.from_user.id):
        discount = await reseller_discount_percent(db)
        products = await resolve_products(config, db)
        p220 = products.get("autolike220")
        p500 = products.get("autolike500")
        ppasse = products.get("passe")
        prent = products.get("bot_rental")

        async def reseller_price(rp):
            if not rp:
                return Decimal("0")
            return (await _effective_price(db, rp, query.from_user.id))[0]

        p220_price = await reseller_price(p220)
        p500_price = await reseller_price(p500)
        passe_price = await reseller_price(ppasse)
        rent_price = await reseller_price(prent)

        await edit_screen(
            query,
            "🤝 <b>ÁREA DO REVENDEDOR</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Revenda ativa\n"
            f"💸 Desconto automático: <b>{discount}%</b>\n\n"
            "💰 <b>Sua tabela atual:</b>\n"
            f"❤️ Auto Like 220: <b>R$ {format_price(p220_price)}/dia</b>\n"
            f"💎 Auto Like 500: <b>R$ {format_price(p500_price)}/dia</b>\n"
            f"🎟️ Passe de Elite: <b>R$ {format_price(passe_price)}</b>\n"
            f"🤖 Aluguel do bot: <b>R$ {format_price(rent_price)}</b>\n\n"
            "Os valores já entram com desconto automaticamente no checkout.",
            reseller_area_menu(),
        )
        await query.answer()
        return

    rp = await resolve_product(config, db, "reseller_access")
    if not rp or rp.price <= 0:
        await edit_screen(
            query,
            "🤝 <b>REVENDA</b>\n\n"
            "O acesso de revendedor ainda não está disponível para compra.",
            back_home(),
        )
        await query.answer()
        return

    discount = await reseller_discount_percent(db)
    await edit_screen(
        query,
        "🤝 <b>ACESSO REVENDA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Depois da confirmação, sua conta recebe automaticamente a "
        "<b>tabela de revendedor</b>.\n\n"
        f"💸 Desconto atual: <b>{discount}%</b> nos produtos elegíveis\n"
        f"💰 Acesso: <b>R$ {format_price(rp.price)}</b>\n\n"
        "O acesso fica vinculado ao seu Telegram.",
        checkout_menu("reseller_access", allow_balance=True),
    )
    await query.answer()


@router.callback_query(F.data == "shop:balance")
async def shop_balance(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    balance = _from_cents(await db.get_balance_cents(query.from_user.id))
    await state.set_state(CustomerPlanFlow.waiting_balance_amount)
    await query.answer()
    await query.message.answer(
        "💰 <b>ADICIONAR SALDO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👛 Saldo atual: <b>R$ {format_price(balance)}</b>\n\n"
        "Digite o valor que deseja adicionar via PIX.\n"
        "Mínimo: <b>R$ 1,00</b> · Máximo: <b>R$ 5.000,00</b>\n"
        "\n🔧 REF: BALANCE",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Ex.: 20,00",
        ),
    )


async def _balance_amount_response(
    message: Message,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip().replace("R$", "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.reply("⚠️ Valor inválido. Exemplo: <code>20,00</code>.")
        return
    if amount < Decimal("1.00") or amount > Decimal("5000.00"):
        await message.reply("⚠️ Escolha um valor entre <b>R$ 1,00 e R$ 5.000,00</b>.")
        return

    await state.clear()
    cents = _to_cents(amount)
    await message.answer(
        "💰 <b>CONFIRMAR RECARGA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Valor: <b>R$ {format_price(amount)}</b>\n\n"
        "O saldo será creditado automaticamente quando o PIX for aprovado.",
        reply_markup=checkout_menu(
            "balance_topup",
            ref_value=str(cents),
            allow_balance=False,
        ),
    )


@router.message(F.reply_to_message.text.regexp(r"REF:\s*BALANCE"))
async def balance_amount_reply(message: Message, state: FSMContext) -> None:
    await _balance_amount_response(message, state)


@router.message(CustomerPlanFlow.waiting_balance_amount, F.text.regexp(r"^(?!/).+"))
async def balance_amount_state(message: Message, state: FSMContext) -> None:
    await _balance_amount_response(message, state)


@router.callback_query(F.data == "shop:rent")
async def shop_rent(
    query: CallbackQuery,
    db: Database,
    config: Config,
) -> None:
    rp = await resolve_product(config, db, "bot_rental")
    if not rp:
        return await query.answer("Produto indisponível.", show_alert=True)
    price, is_reseller, discount = await _effective_price(
        db, rp, query.from_user.id
    )
    if price <= 0:
        await edit_screen(
            query,
            "🤖 <b>ALUGAR BOT</b>\n\n"
            "O aluguel ainda não está disponível para compra.",
            back_home(),
        )
        await query.answer()
        return

    reseller_line = (
        f"\n🤝 Desconto revenda: <b>{discount}%</b>"
        if is_reseller and discount > 0
        else ""
    )
    await edit_screen(
        query,
        "🤖 <b>ALUGAR AURORA SYSTEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📆 Período: <b>30 dias</b>\n"
        "👥 Uso: <b>1 grupo</b>\n"
        "❤️ O bot ficará disponível no grupo para os comandos liberados.\n\n"
        f"💰 Valor: <b>R$ {format_price(price)}</b>"
        f"{reseller_line}\n\n"
        "Após o pagamento, você receberá o botão para adicionar o bot ao grupo.",
        checkout_menu("bot_rental", allow_balance=True),
    )
    await query.answer()


def _parse_checkout(data: str, prefix: str) -> tuple[str, str]:
    raw = data[len(prefix):]
    parts = raw.split(":", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


async def _checkout_details(
    db: Database,
    config: Config,
    user_id: int,
    code: str,
    ref_value: str,
) -> tuple[Decimal, str, str, int] | None:
    rp = await resolve_product(config, db, code)
    if not rp:
        return None

    if code == "balance_topup":
        try:
            cents = int(ref_value)
        except ValueError:
            return None
        if cents < 100 or cents > 500000:
            return None
        amount = _from_cents(cents)
        return amount, f"Adicionar saldo • R$ {format_price(amount)}", "", 0

    price, _, _ = await _effective_price(db, rp, user_id)
    if price <= 0:
        return None

    if code in AUTOLIKE_CODES:
        try:
            days = int(ref_value)
        except ValueError:
            return None
        if not 1 <= days <= 90 or not rp.service_ready or rp.stock == 0:
            return None
        amount = _money(price * Decimal(days))
        return amount, f"{rp.title} • {days} dias", "", days

    if code == "passe":
        uid = clean_game_id(ref_value)
        if not is_valid_game_id(uid) or rp.stock == 0:
            return None
        return price, rp.title, uid, 0

    if code in {"reseller_access", "bot_rental"}:
        if rp.stock == 0:
            return None
        return price, rp.title, "", rp.product.days

    return None


@router.callback_query(F.data.startswith("paypix:"))
async def checkout_pix(
    query: CallbackQuery,
    db: Database,
    config: Config,
    gateway: PaymentGateway,
) -> None:
    code, ref_value = _parse_checkout(query.data, "paypix:")
    details = await _checkout_details(
        db, config, query.from_user.id, code, ref_value
    )
    if not details:
        await query.answer("Pedido inválido ou indisponível.", show_alert=True)
        return

    amount, description, game_id, days = details
    await _create_pix_order(
        query,
        db,
        gateway,
        product_code=code,
        amount=amount,
        description=description,
        game_id=game_id,
        plan_days=days,
    )


@router.callback_query(F.data.startswith("paybal:"))
async def checkout_balance(
    query: CallbackQuery,
    db: Database,
    config: Config,
    delivery: DeliveryService,
) -> None:
    code, ref_value = _parse_checkout(query.data, "paybal:")
    if code == "balance_topup":
        return await query.answer("Use PIX para adicionar saldo.", show_alert=True)

    details = await _checkout_details(
        db, config, query.from_user.id, code, ref_value
    )
    if not details:
        await query.answer("Pedido inválido ou indisponível.", show_alert=True)
        return

    amount, description, game_id, days = details
    cents = _to_cents(amount)

    order_id = await db.create_order(
        user_id=query.from_user.id,
        product_code=code,
        game_id=game_id,
        amount=str(amount),
        plan_days=days,
        paid_with="balance",
    )
    ok, remaining = await db.spend_wallet(
        query.from_user.id,
        cents,
        order_id=order_id,
        description=description,
    )
    if not ok:
        await db.update_order_status(order_id, "failed")
        await query.answer(
            f"Saldo insuficiente. Você tem R$ {format_price(_from_cents(remaining))}.",
            show_alert=True,
        )
        return

    await db.update_order_status(order_id, "paid")
    await query.answer("✅ Pago com saldo!", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await delivery.fulfill_order(order_id)


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
@router.message(PurchaseFlow.waiting_id, F.text.regexp(r"^(?!/).+"))
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

    if rp.delivery == "passe":
        price, is_reseller, discount = await _effective_price(
            db, rp, message.from_user.id
        )
        await state.clear()
        reseller_line = (
            f"🤝 Desconto revenda: <b>{discount}%</b>\n"
            if is_reseller and discount > 0
            else ""
        )
        await checking.edit_text(
            "🧾 <b>CONFIRMAR PEDIDO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Produto: <b>{rp.title}</b>\n"
            f"🎮 UID: <code>{game_id}</code>\n"
            f"{nick_line}"
            f"{reseller_line}"
            f"💰 Valor: <b>R$ {format_price(price)}</b>\n\n"
            "Escolha como deseja pagar:",
            reply_markup=checkout_menu(
                "passe",
                ref_value=game_id,
                allow_balance=True,
            ),
        )
        return

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
