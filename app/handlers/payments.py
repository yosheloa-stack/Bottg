"""Controle de pagamentos de VIP, VIP2 e aluguel do bot (somente donos).

/pagou     — registra um pagamento e já ativa/renova o acesso.
/pagamentos — painel: resumo do mês, últimos pagamentos, vencimentos e clientes.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.config import Config
from app.database import Database
from app.keyboards.inline import add_group_button
from app.owner_auth import resolve_owner_id

logger = logging.getLogger(__name__)

router = Router(name="payments")
TZ_BR = ZoneInfo("America/Sao_Paulo")

KIND_ALIASES = {
    "vip": "vip",
    "vip1": "vip",
    "vip2": "vip2",
    "aluguel": "rental",
    "alugueis": "rental",
    "aluguéis": "rental",
    "rental": "rental",
    "bot": "rental",
    "licenca": "rental",
    "licença": "rental",
}
KIND_LABEL = {"vip": "VIP", "vip2": "VIP2", "rental": "Aluguel"}
METHOD_LABEL = {"manual": "manual", "pix": "PIX no bot", "balance": "saldo"}

_MONEY_RE = re.compile(r"^(?:r\$)?\s*(\d{1,7})(?:[.,](\d{1,2}))?$", re.IGNORECASE)


# ---------- helpers ----------

def _fmt_date(ts: int) -> str:
    if not ts:
        return "Permanente"
    return datetime.fromtimestamp(int(ts), TZ_BR).strftime("%d/%m/%Y")


def _fmt_datetime(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), TZ_BR).strftime("%d/%m/%Y %H:%M")


def _money(cents: int) -> str:
    cents = int(cents or 0)
    reais, cent = divmod(abs(cents), 100)
    txt = f"{reais:,}".replace(",", ".")
    return f"{'-' if cents < 0 else ''}R$ {txt},{cent:02d}"


def _parse_money(raw: str) -> int | None:
    m = _MONEY_RE.match((raw or "").strip())
    if not m:
        return None
    reais = int(m.group(1))
    frac = (m.group(2) or "0").ljust(2, "0")
    return reais * 100 + int(frac)


def _client(user_id: int, username: str | None, first_name: str | None) -> str:
    parts = []
    if username:
        parts.append(f"@{html.escape(username)}")
    elif first_name:
        parts.append(html.escape(first_name))
    else:
        parts.append("sem @")
    parts.append(f"<code>{int(user_id)}</code>")
    return " · ".join(parts)


def _days_left(ts: int) -> str:
    if not ts:
        return "permanente"
    seconds = int(ts) - int(time.time())
    if seconds <= 0:
        return "vencido"
    days = seconds // 86400
    if days == 0:
        return f"{seconds // 3600}h"
    return f"{days}d"


def _payment_line(p: dict) -> str:
    who = _client(p["user_id"], p.get("cur_username"), p.get("cur_first_name"))
    days = int(p.get("days") or 0)
    period = "permanente" if days == 0 else f"{days}d"
    extra = ""
    if p.get("kind") == "rental" and p.get("ref_id"):
        extra = f" · licença #{p['ref_id']}"
    return (
        f"<b>#{p['id']}</b> {KIND_LABEL.get(p['kind'], p['kind'])} · "
        f"<b>{_money(p['amount_cents'])}</b>\n"
        f"   👤 {who}\n"
        f"   📅 {_fmt_datetime(p['created_at'])} · {period} · "
        f"vence {_fmt_date(int(p.get('expires_at') or 0))}{extra} · "
        f"{METHOD_LABEL.get(p.get('method') or '', p.get('method') or '')}"
        + (f"\n   📝 {html.escape(p['note'])}" if p.get("note") else "")
    )


def _month_start() -> int:
    now = datetime.now(TZ_BR)
    return int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


async def _resolve_target(
    raw: str, message: Message, db: Database
) -> tuple[int | None, str | None]:
    """Retorna (user_id, erro)."""
    raw = (raw or "").strip()
    if raw.lstrip("-").isdigit():
        return int(raw), None
    if raw.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", raw):
        user = await db.find_user_by_username(raw)
        if user:
            return int(user["user_id"]), None
        return None, (
            f"⚠️ Não conheço <b>@{html.escape(raw.lstrip('@'))}</b> ainda.\n"
            "Peça para o cliente mandar /start no bot (aí o @ fica salvo) "
            "ou use o ID numérico."
        )
    return None, "⚠️ Informe o @ do cliente ou o ID numérico do Telegram."


async def _notify(bot: Bot, user_id: int, text: str, **kwargs) -> None:
    try:
        await bot.send_message(user_id, text, **kwargs)
    except Exception:  # noqa: BLE001
        # Cliente pode nunca ter iniciado o bot ou ter bloqueado.
        pass


PAGOU_HELP = (
    "💳 <b>REGISTRAR PAGAMENTO</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "<code>/pagou vip @cliente 30 15,00</code>\n"
    "<code>/pagou vip2 123456789 30 20</code>\n"
    "<code>/pagou aluguel @cliente 30 25,00</code> — nova licença\n"
    "<code>/pagou renovar 12 30 25,00</code> — renova a licença #12\n\n"
    "Formato: tipo · @ ou ID · dias (0 = permanente) · valor · observação opcional.\n"
    "VIP/VIP2 são ativados na hora e os dias somam com a validade atual.\n"
    "Também dá para responder uma mensagem do cliente com "
    "<code>/pagou vip 30 15</code>."
)


# ---------- /pagou ----------

@router.message(Command("pagou"))
async def cmd_pagou(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    args = (command.args or "").split()
    if not args:
        await message.answer(PAGOU_HELP)
        return

    action = args[0].lower()

    if action in {"renovar", "renova"}:
        await _renew_rental(message, args[1:], db, bot, owner_id)
        return

    kind = KIND_ALIASES.get(action)
    if not kind:
        await message.answer(PAGOU_HELP)
        return

    rest = args[1:]
    reply_user = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else None
    )
    # Respondendo a mensagem do cliente: o alvo é quem enviou a mensagem.
    if reply_user and not reply_user.is_bot and (not rest or not _looks_like_target(rest[0], rest)):
        await db.track_user(reply_user.id, reply_user.username, reply_user.first_name)
        target = int(reply_user.id)
    else:
        if not rest:
            await message.answer(PAGOU_HELP)
            return
        target, err = await _resolve_target(rest[0], message, db)
        if err:
            await message.answer(err)
            return
        rest = rest[1:]

    if len(rest) < 2:
        await message.answer("⚠️ Faltou dias e valor.\n\n" + PAGOU_HELP)
        return
    try:
        days = int(rest[0])
    except ValueError:
        await message.answer("⚠️ Dias inválidos. Ex.: <code>30</code> (0 = permanente).")
        return
    if days < 0 or days > 3650 or (kind == "rental" and days == 0):
        await message.answer("⚠️ Use de 1 a 3650 dias (VIP aceita 0 = permanente).")
        return
    cents = _parse_money(rest[1])
    if cents is None:
        await message.answer("⚠️ Valor inválido. Ex.: <code>15</code> ou <code>15,90</code>.")
        return
    note = " ".join(rest[2:]).strip() or None

    if kind in {"vip", "vip2"}:
        expires_at = await db.extend_vip_access(kind, target, days, owner_id)
        pay_id = await db.record_payment(
            kind, target, cents,
            days=days, expires_at=expires_at, method="manual",
            note=note, created_by=owner_id,
        )
        user = await db.get_user(target) or {}
        await message.answer(
            f"✅ <b>Pagamento #{pay_id} registrado</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{KIND_LABEL[kind]} ativo\n"
            f"👤 {_client(target, user.get('username'), user.get('first_name'))}\n"
            f"💰 {_money(cents)} · {('permanente' if days == 0 else f'{days} dias')}\n"
            f"📅 Válido até: <b>{_fmt_date(expires_at)}</b>"
        )
        if kind == "vip":
            perk = "Agora você pode usar <code>/like</code> sem limite."
        else:
            perk = "Agora você tem acesso ao <code>/like2</code>."
        await _notify(
            bot,
            target,
            f"💎 <b>Pagamento confirmado — {KIND_LABEL[kind]} ativo!</b>\n\n"
            f"{perk}\n"
            f"📅 Validade: <b>{_fmt_date(expires_at)}</b>",
        )
        return

    # Aluguel novo: cria pedido manual + licença, igual à compra pela loja.
    order_id = await db.create_order(
        user_id=target,
        product_code="bot_rental",
        game_id="",
        amount=f"{cents / 100:.2f}",
        plan_days=days,
        metadata=json.dumps({"manual_by": owner_id}),
        paid_with="manual",
    )
    rental_id = await db.create_bot_rental(order_id, target, days=days)
    await db.update_order_status(order_id, "delivered", "pagamento manual")
    rental = await db.get_bot_rental(rental_id) or {}
    expires_at = int(rental.get("expires_at") or 0)
    pay_id = await db.record_payment(
        "rental", target, cents,
        days=days, expires_at=expires_at, method="manual",
        order_id=order_id, ref_id=rental_id, note=note, created_by=owner_id,
    )
    user = await db.get_user(target) or {}
    await message.answer(
        f"✅ <b>Pagamento #{pay_id} registrado</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Aluguel · licença <code>#{rental_id}</code>\n"
        f"👤 {_client(target, user.get('username'), user.get('first_name'))}\n"
        f"💰 {_money(cents)} · {days} dias\n"
        f"📅 Válido até: <b>{_fmt_date(expires_at)}</b>\n\n"
        "O cliente recebeu o botão para adicionar o bot ao grupo.\n"
        f"No grupo ele também pode usar <code>/ativarbot {rental_id}</code>."
    )
    try:
        me = await bot.get_me()
        markup = add_group_button(f"https://t.me/{me.username}?startgroup=rent_{rental_id}")
    except Exception:  # noqa: BLE001
        markup = None
    await _notify(
        bot,
        target,
        "🤖 <b>ALUGUEL CONFIRMADO!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📆 Período: <b>{days} dias</b>\n"
        "👥 Licença: <b>1 grupo</b>\n"
        f"📄 Código da licença: <code>#{rental_id}</code>\n\n"
        "Toque no botão abaixo, escolha o seu grupo e adicione o bot. "
        "A licença será vinculada automaticamente.\n\n"
        "Se não ativar sozinho, use no grupo:\n"
        f"<code>/ativarbot {rental_id}</code>",
        reply_markup=markup,
    )


def _looks_like_target(first: str, rest: list[str]) -> bool:
    """Ao responder mensagem, decide se o 1º argumento ainda é o cliente.

    Com 3+ argumentos numéricos (ex.: ``123456789 30 15``) o primeiro é o ID;
    com @ é sempre o cliente; caso contrário são dias/valor.
    """
    if first.startswith("@"):
        return True
    return first.isdigit() and len(rest) >= 3 and len(first) >= 5


async def _renew_rental(
    message: Message, args: list[str], db: Database, bot: Bot, owner_id: int
) -> None:
    if len(args) < 3:
        await message.answer(
            "Use: <code>/pagou renovar LICENÇA DIAS VALOR</code>\n"
            "Ex.: <code>/pagou renovar 12 30 25,00</code>"
        )
        return
    raw_id = args[0].lstrip("#")
    if not raw_id.isdigit():
        await message.answer("⚠️ Número da licença inválido.")
        return
    try:
        days = int(args[1])
    except ValueError:
        days = 0
    if days < 1 or days > 3650:
        await message.answer("⚠️ Use de 1 a 3650 dias.")
        return
    cents = _parse_money(args[2])
    if cents is None:
        await message.answer("⚠️ Valor inválido. Ex.: <code>25</code> ou <code>25,00</code>.")
        return
    rental_id = int(raw_id)
    rental = await db.get_bot_rental(rental_id)
    if not rental:
        await message.answer(f"⚠️ Licença <code>#{rental_id}</code> não encontrada.")
        return
    expires_at = await db.extend_bot_rental(rental_id, days) or 0
    target = int(rental["user_id"])
    note = " ".join(args[3:]).strip() or "renovação"
    pay_id = await db.record_payment(
        "rental", target, cents,
        days=days, expires_at=expires_at, method="manual",
        ref_id=rental_id, note=note, created_by=owner_id,
    )
    user = await db.get_user(target) or {}
    await message.answer(
        f"✅ <b>Pagamento #{pay_id} registrado</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Licença <code>#{rental_id}</code> renovada (+{days} dias)\n"
        f"👤 {_client(target, user.get('username'), user.get('first_name'))}\n"
        f"💰 {_money(cents)}\n"
        f"📅 Válida até: <b>{_fmt_date(expires_at)}</b>"
    )
    await _notify(
        bot,
        target,
        "🤖 <b>Aluguel renovado!</b>\n\n"
        f"📄 Licença <code>#{rental_id}</code>\n"
        f"📅 Válida até: <b>{_fmt_date(expires_at)}</b>",
    )


# ---------- /pagamentos ----------

PAINEL_HELP = (
    "<b>Comandos</b>\n"
    "<code>/pagamentos vip</code> · <code>vip2</code> · <code>aluguel</code> — por tipo\n"
    "<code>/pagamentos @cliente</code> — histórico do cliente\n"
    "<code>/pagamentos vencendo 7</code> — vencem nos próximos dias\n"
    "<code>/pagamentos clientes</code> — clientes que já pagaram\n"
    "<code>/pagamentos licencas</code> — aluguéis ativos\n"
    "<code>/pagamentos apagar 15</code> — apaga o lançamento #15\n"
    "<code>/pagou</code> — registrar um pagamento"
)


async def _send_long(message: Message, lines: list[str]) -> None:
    chunk: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > 3800 and chunk:
            await message.answer("\n".join(chunk), disable_web_page_preview=True)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await message.answer("\n".join(chunk), disable_web_page_preview=True)


def _expiring_line(row: dict) -> str:
    who = _client(row["user_id"], row.get("username"), row.get("first_name"))
    label = KIND_LABEL.get(row["kind"], row["kind"])
    if row["kind"] == "rental":
        label += f" #{row['ref_id']}"
        if not row.get("chat_id"):
            label += " (sem grupo)"
    return (
        f"• {label} — {who}\n"
        f"   vence {_fmt_date(int(row['expires_at']))} ({_days_left(int(row['expires_at']))})"
    )


# Só no privado: a lista tem valores e dados de clientes.
@router.message(Command("pagamentos", "pagamento"), F.chat.type == "private")
async def cmd_pagamentos(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    args = (command.args or "").split()

    if not args:
        await _overview(message, db)
        return

    action = args[0].lower()

    if action in KIND_ALIASES:
        kind = KIND_ALIASES[action]
        rows = await db.list_payments(kind=kind, limit=30)
        if not rows:
            await message.answer(f"{KIND_LABEL[kind]}: nenhum pagamento registrado.")
            return
        totals = await db.payment_totals(0)
        month = await db.payment_totals(_month_start())
        lines = [
            f"<b>PAGAMENTOS — {KIND_LABEL[kind]}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📆 Este mês: <b>{_money(month.get(kind, {}).get('cents', 0))}</b> "
            f"({month.get(kind, {}).get('count', 0)})",
            f"📈 Total: <b>{_money(totals.get(kind, {}).get('cents', 0))}</b> "
            f"({totals.get(kind, {}).get('count', 0)})",
            "",
        ]
        lines += [_payment_line(p) for p in rows]
        await _send_long(message, lines)
        return

    if action in {"vencendo", "vence", "vencimentos"}:
        try:
            within = int(args[1]) if len(args) > 1 else 7
        except ValueError:
            within = 7
        within = max(1, min(within, 365))
        rows = await db.expiring_access(within)
        if not rows:
            await message.answer(f"✅ Nada vence nos próximos {within} dias.")
            return
        lines = [f"⏰ <b>VENCEM EM ATÉ {within} DIAS</b>", "━━━━━━━━━━━━━━━━━━━━"]
        lines += [_expiring_line(r) for r in rows]
        await _send_long(message, lines)
        return

    if action in {"clientes", "cliente"} and len(args) == 1:
        rows = await db.payment_clients(limit=60)
        if not rows:
            await message.answer("Nenhum cliente com pagamento registrado ainda.")
            return
        lines = ["👥 <b>CLIENTES QUE PAGARAM</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            kinds = ", ".join(
                KIND_LABEL.get(k, k) for k in sorted(set((r.get("kinds") or "").split(",")))
                if k
            )
            lines.append(
                f"• {_client(r['user_id'], r.get('username'), r.get('first_name'))}\n"
                f"   {kinds} · {r['payments']} pag. · <b>{_money(r['total_cents'])}</b> · "
                f"último {_fmt_date(int(r['last_at']))}"
            )
        await _send_long(message, lines)
        return

    if action in {"licencas", "licenças", "ativos"}:
        rows = await db.active_rentals(limit=60)
        if not rows:
            await message.answer("🤖 Nenhum aluguel ativo.")
            return
        lines = ["🤖 <b>ALUGUÉIS ATIVOS</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            group = (
                html.escape(r.get("chat_title") or str(r["chat_id"]))
                if r.get("chat_id")
                else "⚠️ ainda sem grupo"
            )
            lines.append(
                f"• <b>#{r['id']}</b> — {_client(r['user_id'], r.get('username'), r.get('first_name'))}\n"
                f"   👥 {group}\n"
                f"   vence {_fmt_date(int(r['expires_at']))} ({_days_left(int(r['expires_at']))})"
            )
        await _send_long(message, lines)
        return

    if action in {"apagar", "remover", "del"}:
        if len(args) < 2 or not args[1].lstrip("#").isdigit():
            await message.answer("Use: <code>/pagamentos apagar NÚMERO</code>")
            return
        ok = await db.delete_payment(int(args[1].lstrip("#")))
        await message.answer(
            "🗑️ Lançamento apagado (o acesso do cliente não foi alterado)."
            if ok
            else "⚠️ Lançamento não encontrado."
        )
        return

    # Histórico de um cliente por @ ou ID.
    target, err = await _resolve_target(args[0], message, db)
    if err:
        await message.answer(err + "\n\n" + PAINEL_HELP)
        return
    await _client_history(message, db, target)


async def _overview(message: Message, db: Database) -> None:
    month = await db.payment_totals(_month_start())
    total = await db.payment_totals(0)
    month_sum = sum(v["cents"] for v in month.values())
    total_sum = sum(v["cents"] for v in total.values())

    def kline(kind: str) -> str:
        m = month.get(kind, {"count": 0, "cents": 0})
        return f"{KIND_LABEL[kind]}: <b>{_money(m['cents'])}</b> ({m['count']})"

    lines = [
        "💳 <b>PAGAMENTOS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📆 <b>Este mês:</b> {_money(month_sum)}",
        kline("vip"),
        kline("vip2"),
        kline("rental"),
        f"📈 Total geral: <b>{_money(total_sum)}</b>",
        "",
    ]
    expiring = await db.expiring_access(7)
    if expiring:
        lines.append(f"⏰ <b>Vencem em 7 dias ({len(expiring)})</b>")
        lines += [_expiring_line(r) for r in expiring[:10]]
        if len(expiring) > 10:
            lines.append("   … veja todos com <code>/pagamentos vencendo</code>")
        lines.append("")
    recent = await db.list_payments(limit=8)
    if recent:
        lines.append("🧾 <b>Últimos pagamentos</b>")
        lines += [_payment_line(p) for p in recent]
        lines.append("")
    else:
        lines.append("Nenhum pagamento registrado ainda.\n")
    lines.append(PAINEL_HELP)
    await _send_long(message, lines)


async def _client_history(message: Message, db: Database, user_id: int) -> None:
    user = await db.get_user(user_id) or {}
    vip = await db.get_vip(user_id)
    vip2 = await db.get_vip2(user_id)
    rows = await db.list_payments(user_id=user_id, limit=30)
    total = sum(int(p["amount_cents"]) for p in rows)
    lines = [
        "👤 <b>CLIENTE</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        _client(user_id, user.get("username"), user.get("first_name")),
    ]
    if user.get("first_name") and user.get("username"):
        lines.append(f"Nome: {html.escape(user['first_name'])}")
    lines.append(
        "💎 VIP: "
        + (f"ativo até <b>{_fmt_date(int(vip['expires_at']))}</b>" if vip else "não")
    )
    lines.append(
        "🔥 VIP2: "
        + (f"ativo até <b>{_fmt_date(int(vip2['expires_at']))}</b>" if vip2 else "não")
    )
    rentals = [r for r in await db.active_rentals(limit=500) if int(r["user_id"]) == user_id]
    for r in rentals:
        group = html.escape(r.get("chat_title") or str(r["chat_id"])) if r.get("chat_id") else "sem grupo"
        lines.append(f"🤖 Licença #{r['id']} ({group}) até <b>{_fmt_date(int(r['expires_at']))}</b>")
    lines.append(f"💰 Total pago: <b>{_money(total)}</b> em {len(rows)} pagamento(s)")
    lines.append("")
    lines += [_payment_line(p) for p in rows] or ["Nenhum pagamento registrado."]
    await _send_long(message, lines)
