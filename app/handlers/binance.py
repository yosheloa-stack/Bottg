"""Recarga de saldo em USDT: painel do dono (/binance) e fluxo do cliente."""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import binance_pay as bp
from app.config import Config
from app.database import Database
from app.database.db import BRAZIL_TZ
from app.delivery import DeliveryService
from app.owner_auth import resolve_owner_id
from app.services.binance_mail import BinanceMailError, fetch_binance_mails, parse_amount
from app.states import BinanceOwner, BinanceTopup
from app.utils import format_price

logger = logging.getLogger(__name__)

router = Router(name="binance")


# ---------------------------------------------------------------- cliente
# Textos em português; o filtro de saída traduz linha a linha (translations.py).

UNAVAILABLE = "Pagamento em USDT indisponível no momento. Tente o PIX ou fale com o suporte."
NOT_PENDING = "Este pedido não está mais aguardando pagamento."
CANCELLED = "Pedido cancelado."


def balance_method_text(balance: str) -> str:
    return (
        "<b>ADICIONAR SALDO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Saldo atual: <b>R$ {balance}</b>\n\n"
        "Escolha como deseja pagar:"
    )


def topup_method_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="PIX", callback_data="topup:pix"),
        InlineKeyboardButton(text="Binance (USDT)", callback_data="topup:usdt"),
    )
    kb.row(InlineKeyboardButton(text="Voltar", callback_data="nav:v2:home"))
    return kb.as_markup()


def _usdt(value: Decimal) -> str:
    return f"{value:.2f}"


@router.callback_query(F.data == "topup:usdt")
async def topup_usdt(query: CallbackQuery, state: FSMContext, db: Database) -> None:
    rate = await bp.current_rate(db) if await bp.is_available(db) else None
    if rate is None:
        await query.answer(UNAVAILABLE, show_alert=True)
        return
    await query.answer()
    await state.set_state(BinanceTopup.waiting_amount)
    await query.message.answer(
        "<b>RECARGA EM USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Cotação: <b>1 USDT = R$ {format_price(rate)}</b>\n\n"
        "Digite quantos USDT você vai enviar.\n"
        f"Mínimo: <b>{_usdt(bp.MIN_USDT)} USDT</b> · Máximo: <b>{_usdt(bp.MAX_USDT)} USDT</b>"
    )


@router.message(BinanceTopup.waiting_amount, F.text.regexp(r"^(?!/).+"))
async def topup_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").upper().replace("USDT", "").replace(" ", "")
    amount = parse_amount(raw)
    if amount is None or not bp.MIN_USDT <= amount <= bp.MAX_USDT:
        await message.reply(
            f"Valor inválido. Digite um número entre {_usdt(bp.MIN_USDT)} "
            f"e {_usdt(bp.MAX_USDT)}, por exemplo <code>10</code>."
        )
        return
    await state.update_data(usdt=str(amount))
    await state.set_state(BinanceTopup.waiting_user)
    await message.answer(ASK_USER)


def binance_unavailable_text() -> str:
    return UNAVAILABLE


ASK_USER = (
    "Agora envie o seu <b>nome de usuário ou Pay ID</b> na Binance, "
    "da conta que vai fazer o pagamento."
)


async def ask_binance_user_for_plan(
    message: Message,
    state: FSMContext,
    *,
    product_code: str,
    description: str,
    amount_brl: Decimal,
    usdt: Decimal,
    game_id: str,
    plan_days: int,
) -> None:
    """Plano com preço fixo: guarda o pedido e só pede o usuário da Binance."""
    await state.set_state(BinanceTopup.waiting_user)
    await state.update_data(
        plan_code=product_code,
        plan_description=description,
        plan_brl=str(amount_brl),
        usdt=str(usdt),
        plan_game_id=game_id,
        plan_days=plan_days,
    )
    await message.answer(
        "<b>PAGAMENTO EM USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Produto: <b>{description}</b>\n"
        f"Valor fixo: <b>{_usdt(usdt)} USDT</b>\n\n"
        + ASK_USER
    )


@router.message(BinanceTopup.waiting_user, F.text.regexp(r"^(?!/).+"))
async def topup_user(message: Message, state: FSMContext, db: Database) -> None:
    binance_user = (message.text or "").strip().lstrip("@")
    if not 3 <= len(binance_user) <= 40:
        await message.reply("Envie um nome de usuário ou Pay ID válido (3 a 40 caracteres).")
        return

    data = await state.get_data()
    await state.clear()
    settings = await bp.load_settings(db)
    rate = await bp.current_rate(db) if settings.ready else None
    if rate is None:
        await message.answer(UNAVAILABLE)
        return

    plan_code = data.get("plan_code")
    if plan_code:
        order_id, usdt, brl = await bp.create_binance_order(
            db,
            message.from_user.id,
            Decimal(data.get("usdt") or "0"),
            binance_user,
            rate,
            product_code=plan_code,
            amount_brl=Decimal(data.get("plan_brl") or "0"),
            game_id=data.get("plan_game_id") or "",
            plan_days=int(data.get("plan_days") or 0),
        )
        detail_line = f"Produto: <b>{data.get('plan_description') or ''}</b>\n"
    else:
        order_id, usdt, brl = await bp.create_binance_order(
            db,
            message.from_user.id,
            Decimal(data.get("usdt") or "0"),
            binance_user,
            rate,
        )
        detail_line = f"Saldo que você recebe: <b>R$ {format_price(brl)}</b>\n"

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Copiar valor", copy_text=CopyTextButton(text=_usdt(usdt))),
        InlineKeyboardButton(text="Copiar Pay ID", copy_text=CopyTextButton(text=settings.pay_id)),
    )
    kb.row(InlineKeyboardButton(text="Já paguei / verificar", callback_data=f"usdtchk:{order_id}"))
    kb.row(InlineKeyboardButton(text="Cancelar pedido", callback_data=f"usdtcancel:{order_id}"))
    text = (
        "<b>PAGAMENTO EM USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Pedido: <code>#{order_id}</code>\n"
        f"{detail_line}"
        f"Envie exatamente: <b>{_usdt(usdt)} USDT</b>\n"
        f"Para o Pay ID: <code>{html.escape(settings.pay_id)}</code>\n\n"
        "<b>Como pagar:</b>\n"
        "1. Abra a Binance e toque em <b>Pay</b> &gt; <b>Enviar</b>.\n"
        + (
            "2. Cole o Pay ID acima ou leia o QR Code.\n"
            if settings.qr_file_id
            else "2. Cole o Pay ID acima.\n"
        )
        + f"3. Envie exatamente <b>{_usdt(usdt)} USDT</b>.\n\n"
        "<b>O valor precisa ser exato: nem a menos, nem a mais.</b>\n"
        "Pagamento com valor diferente não é confirmado automaticamente.\n"
        "A confirmação é automática e leva poucos minutos depois que o pagamento chega.\n\n"
        "Este pedido vale por 24 horas."
    )
    sent = None
    if settings.qr_file_id:
        try:
            sent = await message.answer_photo(
                settings.qr_file_id,
                caption=text,
                reply_markup=kb.as_markup(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao enviar o QR Code da Binance; seguindo só com texto")
    if sent is None:
        sent = await message.answer(text, reply_markup=kb.as_markup())
    try:
        await db.set_order_payment_message(order_id, sent.chat.id, sent.message_id)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao salvar mensagem do pedido USDT %s", order_id)


async def _own_order(query: CallbackQuery, db: Database) -> dict | None:
    try:
        order_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return None
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != query.from_user.id:
        return None
    if order.get("paid_with") != "binance":
        return None
    return order


@router.callback_query(F.data.startswith("usdtchk:"))
async def topup_check(
    query: CallbackQuery,
    bot: Bot,
    db: Database,
    config: Config,
    delivery: DeliveryService,
) -> None:
    order = await _own_order(query, db)
    if not order:
        await query.answer()
        return
    if order["status"] == "delivered":
        await query.answer("Este pedido já foi confirmado.", show_alert=True)
        return
    if order["status"] != "pending":
        await query.answer(NOT_PENDING, show_alert=True)
        return
    await query.answer("Verificando pagamento...")
    try:
        await bp.process_inbox(bot, db, config, delivery, force=True)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao verificar pagamento USDT do pedido %s", order["id"])
    order = await db.get_order(int(order["id"]))
    if order and order["status"] == "pending":
        await query.message.answer(
            "Ainda não encontramos seu pagamento.\n"
            "Ele costuma aparecer em poucos minutos. O bot continua verificando "
            "sozinho e avisa quando o saldo cair."
        )


@router.callback_query(F.data.startswith("usdtcancel:"))
async def topup_cancel(query: CallbackQuery, db: Database) -> None:
    order = await _own_order(query, db)
    if not order:
        await query.answer()
        return
    if order["status"] != "pending":
        await query.answer(NOT_PENDING, show_alert=True)
        return
    await db.update_order_status(int(order["id"]), "cancelled")
    await query.answer(CANCELLED)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await query.message.answer(CANCELLED)


# ---------------------------------------------------------------- dono

FIELDS = {
    "email": (bp.SETTING_EMAIL, "Envie o <b>e-mail do Gmail</b> que recebe as notificações da Binance."),
    "password": (
        bp.SETTING_PASSWORD,
        "Envie a <b>senha de app</b> do Google (16 letras).\n"
        "Crie em: Conta Google > Segurança > Verificação em duas etapas > Senhas de app.\n"
        "A mensagem com a senha será apagada.",
    ),
    "payid": (bp.SETTING_PAY_ID, "Envie o seu <b>Pay ID da Binance</b> (o número que o cliente vai usar para pagar)."),
    "rate": (
        bp.SETTING_RATE,
        "Envie a <b>cotação fixa</b> em reais de 1 USDT, por exemplo <code>5,60</code>.\n"
        "Envie <code>auto</code> para usar a cotação ao vivo da Binance.",
    ),
}


def _is_owner(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


def _mask_email(value: str) -> str:
    if not value:
        return "não configurado"
    name, _, domain = value.partition("@")
    return html.escape(f"{name[:3]}***@{domain}")


async def _panel(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    s = await bp.load_settings(db)
    live = None if s.manual_rate is not None else await bp.current_rate(db)
    if s.manual_rate is not None:
        rate_line = f"R$ {format_price(s.manual_rate)} (fixa)"
    elif live is not None:
        rate_line = f"R$ {format_price(live)} (ao vivo)"
    else:
        rate_line = "indisponível, defina uma cotação fixa"
    pending = len(await db.pending_binance_orders())
    if s.ready:
        status = "Ativo"
    elif s.enabled:
        status = "Ativado, mas falta configuração"
    else:
        status = "Desativado"
    text = (
        "<b>PAGAMENTO BINANCE (USDT) POR E-MAIL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: <b>{status}</b>\n"
        f"E-mail: <b>{_mask_email(s.email)}</b>\n"
        f"Senha de app: <b>{'configurada' if s.password else 'não configurada'}</b>\n"
        f"Pay ID: <b>{html.escape(s.pay_id) or 'não configurado'}</b>\n"
        f"QR Code: <b>{'configurado' if s.qr_file_id else 'não configurado'}</b>\n"
        f"Cotação: <b>{rate_line}</b>\n"
        f"Pedidos aguardando: <b>{pending}</b>\n\n"
        "O bot lê os e-mails de recebimento da Binance a cada 30 segundos "
        "enquanto houver pedido aguardando e credita o saldo do cliente."
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="E-mail", callback_data="bnc:set:email"),
        InlineKeyboardButton(text="Senha de app", callback_data="bnc:set:password"),
    )
    kb.row(
        InlineKeyboardButton(text="Pay ID", callback_data="bnc:set:payid"),
        InlineKeyboardButton(text="Cotação", callback_data="bnc:set:rate"),
    )
    kb.row(InlineKeyboardButton(text="QR Code", callback_data="bnc:qr"))
    kb.row(
        InlineKeyboardButton(
            text="Desativar" if s.enabled else "Ativar",
            callback_data="bnc:toggle",
        ),
        InlineKeyboardButton(text="Testar caixa de entrada", callback_data="bnc:test"),
    )
    kb.row(InlineKeyboardButton(text="Atualizar", callback_data="bnc:panel"))
    return text, kb.as_markup()


@router.message(Command("binance"))
async def cmd_binance(message: Message, db: Database, config: Config, bot: Bot) -> None:
    if await resolve_owner_id(message, bot, config) is None:
        return
    if message.chat.type != "private":
        await message.reply("Por segurança, use <code>/binance</code> no privado do bot.")
        return
    text, kb = await _panel(db)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "bnc:panel")
async def cb_panel(query: CallbackQuery, db: Database, config: Config) -> None:
    if not _is_owner(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    await query.answer()
    text, kb = await _panel(db)
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await query.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("bnc:set:"))
async def cb_set(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not _is_owner(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    field = query.data.split(":", 2)[2]
    if field not in FIELDS:
        await query.answer()
        return
    await query.answer()
    await state.set_state(BinanceOwner.waiting_value)
    await state.update_data(binance_field=field)
    await query.message.answer(FIELDS[field][1] + "\n\nUse /cancel para desistir.")


@router.callback_query(F.data == "bnc:qr")
async def cb_qr(query: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not _is_owner(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    await query.answer()
    await state.set_state(BinanceOwner.waiting_qr)
    await query.message.answer(
        "Envie a <b>foto do seu QR Code</b> de recebimento da Binance "
        "(na Binance: Pay &gt; Receber &gt; salvar imagem).\n"
        "Para remover o QR Code, envie <code>remover</code>.\n\n"
        "Use /cancel para desistir."
    )


@router.message(BinanceOwner.waiting_qr, F.photo)
async def owner_qr_photo(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    if not message.from_user or not _is_owner(message.from_user.id, config):
        return
    await db.set_owner_setting(bp.SETTING_QR, message.photo[-1].file_id)
    await state.clear()
    text, kb = await _panel(db)
    await message.answer("QR Code salvo. Ele vai junto das instruções de pagamento.\n\n" + text, reply_markup=kb)


@router.message(BinanceOwner.waiting_qr, F.text.regexp(r"^(?!/).+"))
async def owner_qr_text(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    if not message.from_user or not _is_owner(message.from_user.id, config):
        return
    if (message.text or "").strip().lower() not in {"remover", "apagar", "remove"}:
        await message.reply("Envie uma foto do QR Code, ou <code>remover</code>.")
        return
    await db.set_owner_setting(bp.SETTING_QR, "")
    await state.clear()
    text, kb = await _panel(db)
    await message.answer("QR Code removido.\n\n" + text, reply_markup=kb)


@router.message(BinanceOwner.waiting_value, F.text.regexp(r"^(?!/).+"))
async def owner_value(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    if not message.from_user or not _is_owner(message.from_user.id, config):
        return
    field = (await state.get_data()).get("binance_field")
    if field not in FIELDS:
        await state.clear()
        return
    key = FIELDS[field][0]
    value = (message.text or "").strip()

    if field == "email":
        value = value.lower()
        if "@" not in value or " " in value:
            await message.reply("E-mail inválido. Envie de novo ou use /cancel.")
            return
    elif field == "password":
        value = value.replace(" ", "")
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            pass
        if len(value) < 8:
            await message.answer("Senha de app inválida. Envie de novo ou use /cancel.")
            return
    elif field == "rate":
        if value.lower() in {"auto", "automatica", "automática", "0"}:
            value = ""
        else:
            rate = parse_amount(value.replace("R$", "").replace(" ", ""))
            if rate is None:
                await message.reply("Cotação inválida. Exemplo: <code>5,60</code>.")
                return
            value = str(rate)
    elif not value or len(value) > 64:
        await message.reply("Valor inválido. Envie de novo ou use /cancel.")
        return

    await db.set_owner_setting(key, value)
    await state.clear()
    text, kb = await _panel(db)
    await message.answer("Salvo.\n\n" + text, reply_markup=kb)


@router.callback_query(F.data == "bnc:toggle")
async def cb_toggle(query: CallbackQuery, db: Database, config: Config) -> None:
    if not _is_owner(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    s = await bp.load_settings(db)
    if s.enabled:
        await db.set_owner_setting(bp.SETTING_ENABLED, "0")
        await query.answer("Desativado.")
    else:
        if not (s.mailbox_ready and s.pay_id):
            await query.answer(
                "Configure e-mail, senha de app e Pay ID antes de ativar.",
                show_alert=True,
            )
            return
        await db.set_owner_setting(bp.SETTING_ENABLED, "1")
        await db.set_owner_setting(bp.SETTING_SINCE, str(int(time.time())))
        await query.answer("Ativado.")
    text, kb = await _panel(db)
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await query.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "bnc:test")
async def cb_test(query: CallbackQuery, db: Database, config: Config) -> None:
    if not _is_owner(query.from_user.id, config):
        await query.answer("Sem permissão.", show_alert=True)
        return
    s = await bp.load_settings(db)
    if not s.mailbox_ready:
        await query.answer("Configure o e-mail e a senha de app primeiro.", show_alert=True)
        return
    await query.answer("Lendo a caixa de entrada...")
    status = await query.message.answer("Conectando ao Gmail...")
    try:
        inbox = await fetch_binance_mails(s.email, s.password, since_days=7, limit=10)
    except BinanceMailError as exc:
        await status.edit_text(f"<b>Falhou.</b>\n{html.escape(str(exc))}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Teste da caixa de entrada falhou")
        await status.edit_text(f"<b>Falhou.</b>\n{html.escape(str(exc))[:300]}")
        return

    lines = [
        "<b>Conexão funcionando.</b>",
        f"Mensagens na caixa de entrada: <b>{inbox.total_messages}</b>",
        f"E-mails da Binance nos últimos 7 dias: <b>{len(inbox.mails)}</b>",
    ]
    for mail in reversed(inbox.mails[-5:]):
        when = datetime.fromtimestamp(mail.date_ts, BRAZIL_TZ).strftime("%d/%m %H:%M")
        usable = mail.authentic and mail.is_receipt and mail.amount is not None
        lines.append(
            "\n"
            f"{when} · {html.escape(mail.subject[:80])}\n"
            f"Autêntico: <b>{'sim' if mail.authentic else 'não'}</b> · "
            f"Valor: <b>{f'{mail.amount} USDT' if mail.amount is not None else 'não achei'}</b>\n"
            f"Remetente lido: <b>{html.escape(mail.payer) or 'não achei'}</b>\n"
            f"Conta como pagamento: <b>{'sim' if usable else 'não'}</b>"
        )
    if not inbox.mails:
        lines.append(
            "\nNenhum e-mail da Binance encontrado. Ative as notificações de "
            "e-mail na Binance para pagamentos recebidos."
        )
    await status.edit_text("\n".join(lines))
