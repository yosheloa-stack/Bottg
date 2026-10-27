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
    main_menu,
    payment_pending,
    reseller_area_menu,
)
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


async def _ack(query: CallbackQuery, text: str | None = None) -> None:
    try:
        await query.answer(text)
    except Exception:  # noqa: BLE001
        logger.debug("Callback já respondido ou expirado: %s", query.data)


async def _customer_callback_error(
    query: CallbackQuery,
    action: str,
    exc: Exception,
) -> None:
    logger.exception(
        "Erro no menu cliente | action=%s user=%s data=%s",
        action,
        query.from_user.id,
        query.data,
        exc_info=exc,
    )
    try:
        await query.message.answer(
            "⚠️ <b>Não consegui abrir essa opção agora.</b>\n\n"
            "Tente novamente pelo <code>/start</code>. "
            "Se continuar, avise o suporte."
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao avisar erro do menu cliente")


async def _remember_payment_message(
    db: Database,
    order_id: int,
    message: Message,
) -> None:
    try:
        await db.set_order_payment_message(
            order_id,
            int(message.chat.id),
            int(message.message_id),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Falha ao salvar referência da mensagem PIX pedido=%s",
            order_id,
        )


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
        await query.message.answer("⚠️ Produto não encontrado.")
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
        return

    if unit_price <= 0:
        await edit_screen(
            query,
            "⚠️ <b>Preço ainda não configurado.</b>\n\n"
            "Esse serviço será liberado em breve.",
            back_home(),
        )
        return

    if rp.stock == 0:
        await edit_screen(query, texts.OUT_OF_STOCK, back_home())
        return

    if code == "autolike220":
        title = "❤️ <b>AUTO LIKE 220</b>"
        rate = "<b>220 likes por dia</b>"
        provider_text = "Envio diário automático às <b>09:00</b>."
    else:
        title = "💎 <b>AUTO LIKE 500</b>"
        rate = "<b>aproximadamente 500 a 1.000 likes por dia</b>"
        provider_text = "Envio diário automático às <b>09:00</b>."

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
        await edit_screen(
            query,
            "⚠️ <b>Mercado Pago ainda não está configurado.</b>\n\n"
            "Tente novamente mais tarde.",
            back_home(),
        )
        return

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
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao gerar PIX para pedido %s", order_id)
        await db.update_order_status(order_id, "failed", str(exc))
        error_text = (
            "❌ <b>Não consegui gerar o PIX.</b>\n\n"
            "O Mercado Pago recusou a criação da cobrança ou a integração "
            "ainda não está pronta.\n\n"
            f"📄 Pedido: <code>#{order_id}</code>\n"
            "Tente novamente em alguns instantes. Se continuar, avise o suporte."
        )
        try:
            await edit_screen(query, error_text, back_home())
        except Exception:
            await query.message.answer(error_text, reply_markup=back_home())
        return

    await db.set_order_payment(order_id, charge.payment_id)

    caption = (
        "💠 <b>PIX GERADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 {description}\n"
        f"💰 Valor: <b>R$ {format_price(amount)}</b>\n\n"
        "📋 Toque em <b>Copiar código PIX</b> para copiar o pagamento.\n"
        "🔄 Depois de pagar, toque em <b>Já paguei / verificar</b>.\n\n"
        "⏳ <i>Aguardando pagamento...</i>"
    )
    kb = payment_pending(charge.payment_id, charge.qr_code)

    if charge.qr_code_base64:
        try:
            img_bytes = base64.b64decode(charge.qr_code_base64)
            photo = BufferedInputFile(img_bytes, filename="pix.png")
            try:
                await query.message.delete()
            except Exception:
                pass
            payment_message = await query.message.answer_photo(
                photo,
                caption=caption,
                reply_markup=kb,
            )
            await _remember_payment_message(db, order_id, payment_message)
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
        payment_message = await msg.answer(caption, reply_markup=kb)
        await _remember_payment_message(db, order_id, payment_message)
    else:
        edited = await msg.edit_text(caption, reply_markup=kb)
        payment_message = edited if isinstance(edited, Message) else msg
        await _remember_payment_message(db, order_id, payment_message)


# ---------- Novo menu cliente ----------
@router.callback_query(F.data == "shop:auto220")
async def shop_auto220(query: CallbackQuery, db: Database, config: Config) -> None:
    await _ack(query)
    try:
        await _show_autolike_category(query, db, config, "autolike220")
    except Exception as exc:  # noqa: BLE001
        await _customer_callback_error(query, "auto220", exc)


@router.callback_query(F.data == "shop:auto500")
async def shop_auto500(query: CallbackQuery, db: Database, config: Config) -> None:
    await _ack(query)
    try:
        await _show_autolike_category(query, db, config, "autolike500")
    except Exception as exc:  # noqa: BLE001
        await _customer_callback_error(query, "auto500", exc)


@router.callback_query(F.data.startswith("days:"))
async def choose_preset_days(
    query: CallbackQuery,
    db: Database,
    config: Config,
) -> None:
    await _ack(query)
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.message.answer("⚠️ Opção inválida.")
        return
    code = parts[1]
    try:
        days = int(parts[2])
    except ValueError:
        await query.message.answer("⚠️ Quantidade de dias inválida.")
        return
    if code not in AUTOLIKE_CODES or not 1 <= days <= 90:
        await query.message.answer("⚠️ Escolha entre 1 e 90 dias.")
        return
    await _show_dynamic_confirmation(
        query, db, config, query.from_user.id, code, days
    )


@router.callback_query(F.data.startswith("dayscustom:"))
async def choose_custom_days(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    await _ack(query)
    code = query.data.split(":", 1)[1]
    if code not in AUTOLIKE_CODES:
        await query.message.answer("⚠️ Produto inválido.")
        return

    await db.set_customer_pending_input(
        query.from_user.id,
        "custom_days",
        code,
        ttl_seconds=600,
    )
    await state.set_state(CustomerPlanFlow.waiting_days)
    await state.update_data(custom_plan_code=code)
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
    F.chat.type == "private",
    F.text.regexp(r"^\s*(?:[1-9]|[1-8][0-9]|90)\s*$"),
)
async def custom_days_persistent_input(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    pending = await db.get_customer_pending_input(message.from_user.id)
    if not pending or pending.get("action") != "custom_days":
        return

    code = str(pending.get("value") or "")
    if code not in AUTOLIKE_CODES:
        await db.clear_customer_pending_input(message.from_user.id)
        await state.clear()
        return

    try:
        days = int((message.text or "").strip())
    except ValueError:
        return

    if not 1 <= days <= 90:
        await message.reply("⚠️ O período permitido é de <b>1 a 90 dias</b>.")
        return

    await db.clear_customer_pending_input(message.from_user.id)
    await state.clear()
    await _show_dynamic_confirmation(
        message,
        db,
        config,
        message.from_user.id,
        code,
        days,
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

    await db.clear_customer_pending_input(message.from_user.id)
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

    await db.clear_customer_pending_input(message.from_user.id)
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
    await _ack(query)
    try:
        rp = await resolve_product(config, db, "passe")
        if not rp:
            await query.message.answer("⚠️ Produto indisponível.")
            return

        price, is_reseller, discount = await _effective_price(
            db, rp, query.from_user.id
        )
        if not rp.service_ready:
            await edit_screen(
                query,
                "⚠️ <b>Passe de Elite temporariamente indisponível.</b>\n\n"
                "A integração de entrega ainda não está configurada.",
                back_home(),
            )
            return

        if price <= 0 or rp.stock == 0:
            await edit_screen(query, texts.OUT_OF_STOCK, back_home())
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
    except Exception as exc:  # noqa: BLE001
        await _customer_callback_error(query, "passe", exc)


@router.callback_query(F.data == "shop:reseller")
async def shop_reseller(
    query: CallbackQuery,
    db: Database,
    config: Config,
) -> None:
    await _ack(query)
    try:
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
            return

        rp = await resolve_product(config, db, "reseller_access")
        if not rp or rp.price <= 0:
            await edit_screen(
                query,
                "🤝 <b>REVENDA</b>\n\n"
                "O acesso de revendedor ainda não está disponível para compra.",
                back_home(),
            )
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
    except Exception as exc:  # noqa: BLE001
        await _customer_callback_error(query, "reseller", exc)


@router.callback_query(F.data == "shop:balance")
async def shop_balance(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    await _ack(query)
    try:
        balance = _from_cents(await db.get_balance_cents(query.from_user.id))
        await state.set_state(CustomerPlanFlow.waiting_balance_amount)
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
    except Exception as exc:  # noqa: BLE001
        await _customer_callback_error(query, "balance", exc)


async def _balance_amount_response(
    message: Message,
    state: FSMContext,
    db: Database,
    gateway: PaymentGateway,
) -> None:
    raw = (
        (message.text or "")
        .strip()
        .replace("R$", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.reply("⚠️ Valor inválido. Exemplo: <code>20,00</code>.")
        return

    if amount < Decimal("1.00") or amount > Decimal("5000.00"):
        await message.reply(
            "⚠️ Escolha um valor entre <b>R$ 1,00 e R$ 5.000,00</b>."
        )
        return

    if not getattr(gateway, "configured", True):
        await message.reply(
            "⚠️ <b>Pagamento temporariamente indisponível.</b>\n\n"
            "O Mercado Pago ainda não está configurado."
        )
        return

    await state.clear()
    status = await message.answer(
        "⏳ <b>Gerando seu PIX...</b>\n"
        f"💰 Valor da recarga: <b>R$ {format_price(amount)}</b>"
    )

    order_id = await db.create_order(
        user_id=message.from_user.id,
        product_code="balance_topup",
        game_id="",
        amount=str(_money(amount)),
        paid_with="pix",
    )

    try:
        charge = await gateway.create_pix(
            amount=_money(amount),
            description=f"Adicionar saldo - R$ {format_price(amount)}",
            external_reference=str(order_id),
            payer_email=f"user{message.from_user.id}@bottg.com",
            payer_name=message.from_user.first_name or "Cliente",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao gerar PIX da recarga pedido=%s", order_id)
        await db.update_order_status(order_id, "failed", str(exc))
        await status.edit_text(
            "❌ <b>Não consegui gerar o PIX.</b>\n\n"
            "O Mercado Pago recusou a criação da cobrança ou a integração "
            "ainda não está pronta.\n\n"
            f"📄 Pedido: <code>#{order_id}</code>\n"
            "Tente novamente. Se continuar, avise o suporte.",
            reply_markup=back_home(),
        )
        return

    await db.set_order_payment(order_id, charge.payment_id)

    caption = (
        "💰 <b>RECARGA DE SALDO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 Pedido: <code>#{order_id}</code>\n"
        f"💵 Valor: <b>R$ {format_price(amount)}</b>\n\n"
        "📋 Toque em <b>Copiar código PIX</b> para copiar o pagamento.\n"
        "🔄 Depois de pagar, toque em <b>Já paguei / verificar</b>.\n\n"
        "⏳ <i>Aguardando pagamento...</i>"
    )
    kb = payment_pending(charge.payment_id, charge.qr_code)

    if charge.qr_code_base64:
        try:
            img_bytes = base64.b64decode(charge.qr_code_base64)
            photo = BufferedInputFile(img_bytes, filename="pix_saldo.png")
            try:
                await status.delete()
            except Exception:
                pass
            payment_message = await message.answer_photo(
                photo,
                caption=caption,
                reply_markup=kb,
            )
            await _remember_payment_message(db, order_id, payment_message)
            return
        except Exception:  # noqa: BLE001
            logger.warning(
                "Falha ao decodificar QR base64 da recarga pedido=%s",
                order_id,
            )

    edited = await status.edit_text(caption, reply_markup=kb)
    payment_message = edited if isinstance(edited, Message) else status
    await _remember_payment_message(db, order_id, payment_message)


@router.message(F.reply_to_message.text.regexp(r"REF:\s*BALANCE"))
async def balance_amount_reply(
    message: Message,
    state: FSMContext,
    db: Database,
    gateway: PaymentGateway,
) -> None:
    await _balance_amount_response(message, state, db, gateway)


@router.message(CustomerPlanFlow.waiting_balance_amount, F.text.regexp(r"^(?!/).+"))
async def balance_amount_state(
    message: Message,
    state: FSMContext,
    db: Database,
    gateway: PaymentGateway,
) -> None:
    await _balance_amount_response(message, state, db, gateway)


@router.callback_query(F.data == "shop:rent")
async def shop_rent(
    query: CallbackQuery,
    db: Database,
    config: Config,
) -> None:
    await _ack(query)
    try:
        rp = await resolve_product(config, db, "bot_rental")
        if not rp:
            await query.message.answer("⚠️ Produto indisponível.")
            return

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
    except Exception as exc:  # noqa: BLE001
        await _customer_callback_error(query, "rent", exc)


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
    await _ack(query, "Gerando PIX...")
    code, ref_value = _parse_checkout(query.data, "paypix:")
    details = await _checkout_details(
        db, config, query.from_user.id, code, ref_value
    )
    if not details:
        await query.message.answer("⚠️ Pedido inválido ou indisponível.")
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
    await _ack(query)
    code, ref_value = _parse_checkout(query.data, "paybal:")
    if code == "balance_topup":
        await query.message.answer("⚠️ Use PIX para adicionar saldo.")
        return

    details = await _checkout_details(
        db, config, query.from_user.id, code, ref_value
    )
    if not details:
        await query.message.answer("⚠️ Pedido inválido ou indisponível.")
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
        await query.message.answer(
            f"⚠️ Saldo insuficiente. Você tem R$ {format_price(_from_cents(remaining))}."
        )
        return

    await db.update_order_status(order_id, "paid")
    await query.message.answer("✅ Pago com saldo!")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await delivery.fulfill_order(order_id)


# ---- 2. Usuário informa o ID do jogo ----
@router.message(PurchaseFlow.waiting_id, F.text.regexp(r"^(?!/).+"))
async def purchase_receive_id(
    message: Message,
    state: FSMContext,
    config: Config,
    db: Database,
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

    if rp.delivery != "passe":
        await state.clear()
        await message.answer(
            "ℹ️ Esse fluxo antigo foi removido. Use o menu principal.",
            reply_markup=main_menu(False),
        )
        return

    checking = await message.answer("⏳ Validando ID...")
    info = await passe.confirmar(game_id, region=config.default_region)

    if not info.ok:
        data = info.data if isinstance(info.data, dict) else {}
        api_message = data.get("mensagem") or data.get("message")
        await checking.edit_text(
            f"❌ {api_message or 'Não foi possível validar esse ID. Confira os números e tente novamente.'}",
            reply_markup=cancel_only(),
        )
        return

    nick = extract_nick(info.data) if info.ok else None
    nick = info.data.get("nickname") or info.data.get("nick") or nick
    level = info.data.get("nivel") or info.data.get("level")
    account_region = info.data.get("regiao") or config.default_region
    nick_line = (
        f"👤 Nick: <b>{nick or 'Não informado'}</b>\n"
        f"🏅 Nível: <b>{level or '—'}</b>\n"
        f"🌎 Região: <b>{account_region}</b>\n"
    )

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


# ---- 4. Usuário clica "Já paguei / verificar" ----
@router.callback_query(F.data.startswith("check:"))
async def cb_check(
    query: CallbackQuery,
    db: Database,
    gateway: PaymentGateway,
    delivery: DeliveryService,
) -> None:
    await _ack(query, "Verificando pagamento...")
    payment_id = query.data.split(":", 1)[1]
    order = await db.get_order_by_payment(payment_id)
    if not order:
        await query.message.answer("⚠️ Pedido não encontrado.")
        return

    if order["status"] == "delivered":
        await query.message.answer("✅ Este pedido já foi entregue!")
        return

    status = await gateway.get_status(payment_id)
    if status == PaymentStatus.APPROVED:
        await delivery.fulfill_order(order["id"])
    elif status in (PaymentStatus.PENDING, PaymentStatus.UNKNOWN):
        await query.message.answer(
            "⏳ Pagamento ainda não identificado. Aguarde alguns segundos e tente de novo."
        )
    else:
        await query.message.answer(
            f"❌ Pagamento {status.value}. Gere um novo pedido se necessário."
        )
