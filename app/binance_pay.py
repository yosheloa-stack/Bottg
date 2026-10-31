"""Recarga de saldo em USDT confirmada pelos e-mails de recebimento da Binance.

O cliente informa quantos USDT vai enviar e o usuário dele na Binance. O bot
cria um pedido pendente com valor único (centavos ajustados se já houver outro
pedido igual), e o monitor lê a caixa de entrada do dono por IMAP. Quando chega
um e-mail autêntico da Binance com o mesmo valor, o saldo em R$ é creditado.
Cada e-mail é registrado uma única vez, então nunca credita em dobro.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation

import aiohttp
from aiogram import Bot

from app.config import Config
from app.database import Database
from app.delivery import DeliveryService
from app.services.binance_mail import (
    BinanceMail,
    BinanceMailError,
    fetch_binance_mails,
    normalize_name,
)

logger = logging.getLogger(__name__)

SETTING_EMAIL = "BINANCE_EMAIL"
SETTING_PASSWORD = "BINANCE_APP_PASSWORD"
SETTING_PAY_ID = "BINANCE_PAY_ID"
SETTING_QR = "BINANCE_QR_FILE_ID"
SETTING_RATE = "BINANCE_USDT_RATE"
SETTING_ENABLED = "BINANCE_ENABLED"
# Só e-mails a partir deste momento contam, para não cruzar pagamentos antigos.
SETTING_SINCE = "BINANCE_SINCE_TS"

MIN_USDT = Decimal("1.00")
MAX_USDT = Decimal("1000.00")
ORDER_TTL = 24 * 60 * 60
# Tolerância para o relógio do e-mail vir um pouco antes da criação do pedido.
ORDER_CLOCK_SKEW = 15 * 60
# Dois pedidos pendentes com o mesmo valor e sem nome no e-mail: espera este
# tempo (o pedido errado pode ser cancelado) antes de pedir ajuda ao dono.
UNMATCHED_GRACE = 6 * 60 * 60
# Janela para ligar um pagamento atrasado a um pedido expirado ou cancelado.
LATE_PAYMENT_WINDOW = 48 * 60 * 60
POLL_INTERVAL = 30
MIN_FETCH_SPACING = 10

_lock = asyncio.Lock()
_last_fetch = 0.0
_rate_cache: tuple[float, Decimal] | None = None
# Último estado lido do banco, para os teclados (síncronos) saberem se mostram o botão.
_available = False


@dataclass
class BinanceSettings:
    email: str
    password: str
    pay_id: str
    qr_file_id: str
    manual_rate: Decimal | None
    enabled: bool
    since_ts: int

    @property
    def mailbox_ready(self) -> bool:
        return bool(self.email and self.password)

    @property
    def ready(self) -> bool:
        return self.enabled and self.mailbox_ready and bool(self.pay_id)


async def load_settings(db: Database) -> BinanceSettings:
    async def get(key: str) -> str:
        return (await db.get_owner_setting(key) or "").strip()

    rate_raw = await get(SETTING_RATE)
    try:
        manual_rate = Decimal(rate_raw.replace(",", ".")) if rate_raw else None
        if manual_rate is not None and manual_rate <= 0:
            manual_rate = None
    except (InvalidOperation, ValueError):
        manual_rate = None
    try:
        since_ts = int(await get(SETTING_SINCE) or 0)
    except ValueError:
        since_ts = 0
    global _available
    settings = BinanceSettings(
        email=await get(SETTING_EMAIL),
        password=await get(SETTING_PASSWORD),
        pay_id=await get(SETTING_PAY_ID),
        qr_file_id=await get(SETTING_QR),
        manual_rate=manual_rate,
        enabled=(await get(SETTING_ENABLED)) == "1",
        since_ts=since_ts,
    )
    _available = settings.ready
    return settings


def cached_available() -> bool:
    return _available


async def is_available(db: Database) -> bool:
    return (await load_settings(db)).ready


async def _live_rate() -> Decimal | None:
    """Cotação USDT/BRL pública da Binance, guardada por 5 minutos."""
    global _rate_cache
    now = time.time()
    if _rate_cache and now - _rate_cache[0] < 300:
        return _rate_cache[1]
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "USDTBRL"},
            ) as resp:
                data = await resp.json(content_type=None)
        value = Decimal(str(data["price"]))
        if value <= 0:
            return None
    except Exception:  # noqa: BLE001
        logger.warning("Não consegui consultar a cotação USDT/BRL", exc_info=True)
        return _rate_cache[1] if _rate_cache else None
    _rate_cache = (now, value)
    return value


async def current_rate(db: Database) -> Decimal | None:
    """R$ por 1 USDT: valor fixo do dono ou, sem ele, a cotação ao vivo."""
    settings = await load_settings(db)
    if settings.manual_rate is not None:
        return settings.manual_rate
    return await _live_rate()


def order_meta(order: dict) -> dict:
    try:
        data = json.loads(order.get("metadata") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _order_usdt(order: dict) -> Decimal | None:
    try:
        return Decimal(str(order_meta(order).get("usdt"))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def usdt_for_brl(brl: Decimal, rate: Decimal) -> Decimal:
    """Preço fixo do plano em USDT, arredondado para cima no centavo."""
    return (brl / rate).quantize(Decimal("0.01"), rounding=ROUND_UP)


async def create_binance_order(
    db: Database,
    user_id: int,
    usdt: Decimal,
    binance_user: str,
    rate: Decimal,
    *,
    product_code: str = "balance_topup",
    amount_brl: Decimal | None = None,
    game_id: str = "",
    plan_days: int = 0,
) -> tuple[int, Decimal, Decimal]:
    """Cria o pedido e devolve (id, USDT exato a enviar, R$ do pedido).

    Recarga: o R$ creditado sai do USDT. Plano: o R$ é o preço do plano.
    Se outro pedido pendente já usa o mesmo valor, soma 1 centavo para
    cada pagamento ser identificado sem confusão.
    """
    async with _lock:
        taken = {
            _order_usdt(o)
            for o in await db.pending_binance_orders()
            if time.time() - int(o.get("created_at") or 0) < ORDER_TTL
        }
        usdt = usdt.quantize(Decimal("0.01"))
        while usdt in taken:
            usdt += Decimal("0.01")
        if amount_brl is None:
            brl = (usdt * rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        else:
            brl = amount_brl.quantize(Decimal("0.01"))
        metadata = json.dumps(
            {"usdt": str(usdt), "binance_user": binance_user, "rate": str(rate)}
        )
        order_id = await db.create_order(
            user_id=user_id,
            product_code=product_code,
            game_id=game_id,
            amount=str(brl),
            plan_days=plan_days,
            metadata=metadata,
            paid_with="binance",
        )
    return order_id, usdt, brl


def _match_order(mail: BinanceMail, orders: list[dict]) -> dict | None:
    candidates = [
        o
        for o in orders
        if _order_usdt(o) == mail.amount
        and int(o.get("created_at") or 0) - ORDER_CLOCK_SKEW <= mail.date_ts
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        text = normalize_name(f"{mail.payer}\n{mail.text}")
        named = [
            o
            for o in candidates
            if len(normalize_name(order_meta(o).get("binance_user", ""))) >= 3
            and normalize_name(order_meta(o).get("binance_user", "")) in text
        ]
        if len(named) == 1:
            return named[0]
    return None


def _orders_line(orders: list[dict]) -> str:
    return ", ".join(
        f"#{o['id']} (cliente <code>{o['user_id']}</code>, {o['status']})"
        for o in orders
    )


async def _notify_owners(bot: Bot, config: Config, text: str) -> None:
    for owner_id in config.admin_ids:
        try:
            await bot.send_message(owner_id, text)
        except Exception:  # noqa: BLE001
            logger.warning("Não consegui avisar o dono %s", owner_id)


async def process_inbox(
    bot: Bot,
    db: Database,
    config: Config,
    delivery: DeliveryService,
    *,
    force: bool = False,
) -> int:
    """Lê a caixa de entrada e credita os pagamentos encontrados. Retorna quantos."""
    global _last_fetch
    async with _lock:
        settings = await load_settings(db)
        if not settings.ready:
            return 0

        now = time.time()
        orders = []
        for order in await db.pending_binance_orders():
            if now - int(order.get("created_at") or now) > ORDER_TTL:
                await db.update_order_status(int(order["id"]), "expired")
            else:
                orders.append(order)
        # Pedido expirado/cancelado recente ainda pode receber pagamento atrasado.
        recently_closed = [
            o
            for o in await db.recent_binance_orders(int(now - LATE_PAYMENT_WINDOW))
            if o["status"] in {"expired", "cancelled"}
        ]
        if not orders and not recently_closed:
            return 0
        if not force and now - _last_fetch < MIN_FETCH_SPACING:
            return 0
        _last_fetch = now

        inbox = await fetch_binance_mails(settings.email, settings.password)
        to_fulfill: list[int] = []
        for mail in inbox.mails:
            if mail.date_ts < settings.since_ts - ORDER_CLOCK_SKEW:
                continue
            if await db.binance_email_seen(mail.key):
                continue
            if not mail.authentic or not mail.is_receipt or mail.amount is None:
                await db.mark_binance_email(mail.key, None, "ignored")
                continue

            order = _match_order(mail, orders)
            if order:
                if await db.mark_binance_email(mail.key, int(order["id"]), "credited"):
                    orders.remove(order)
                    to_fulfill.append(int(order["id"]))
                    logger.info(
                        "Pagamento USDT confirmado por e-mail | pedido=%s valor=%s",
                        order["id"],
                        mail.amount,
                    )
                continue

            # Sem pedido pendente casando. Só avisa o dono quando o valor bate
            # com um pedido do bot; outros recebimentos da conta são ignorados.
            related = [
                o
                for o in await db.recent_binance_orders(
                    int(mail.date_ts - LATE_PAYMENT_WINDOW)
                )
                if _order_usdt(o) == mail.amount
                and int(o.get("created_at") or 0) - ORDER_CLOCK_SKEW <= mail.date_ts
            ]
            ambiguous = [o for o in related if o["status"] == "pending"]
            late = [o for o in related if o["status"] in {"expired", "cancelled"}]
            if ambiguous:
                if now - mail.date_ts <= UNMATCHED_GRACE:
                    continue
                if await db.mark_binance_email(mail.key, None, "ambiguous"):
                    await _notify_owners(
                        bot,
                        config,
                        "<b>Pagamento USDT sem identificação</b>\n"
                        f"Valor: <b>{mail.amount} USDT</b>\n"
                        f"Pedidos com esse valor: {_orders_line(ambiguous)}\n\n"
                        "O e-mail não diz quem pagou. Confira na Binance e "
                        "credite o cliente certo manualmente.",
                    )
            elif late:
                if await db.mark_binance_email(mail.key, None, "late"):
                    await _notify_owners(
                        bot,
                        config,
                        "<b>Pagamento USDT de pedido encerrado</b>\n"
                        f"Valor: <b>{mail.amount} USDT</b>\n"
                        f"Pedidos: {_orders_line(late)}\n\n"
                        "O pagamento chegou depois que o pedido expirou ou foi "
                        "cancelado. Se for desse cliente, credite manualmente.",
                    )
            else:
                await db.mark_binance_email(mail.key, None, "unrelated")

    for order_id in to_fulfill:
        await delivery.fulfill_order(order_id)
    return len(to_fulfill)


async def watch_binance_payments(
    bot: Bot,
    db: Database,
    config: Config,
    delivery: DeliveryService,
) -> None:
    """Confere a caixa de entrada enquanto houver recargas USDT pendentes."""
    last_error = ""
    while True:
        try:
            await process_inbox(bot, db, config, delivery)
            last_error = ""
        except BinanceMailError as exc:
            if str(exc) != last_error:
                logger.warning("Leitura do e-mail de pagamentos falhou: %s", exc)
                last_error = str(exc)
        except Exception:  # noqa: BLE001
            logger.exception("Erro no monitor de pagamentos USDT por e-mail")
        await asyncio.sleep(POLL_INTERVAL)
