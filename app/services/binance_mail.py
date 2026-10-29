"""Leitura por IMAP dos e-mails de recebimento da Binance (Gmail + senha de app).

Só considera autênticos os e-mails que o próprio Gmail marcou com DKIM ou
DMARC aprovados para um domínio binance.com, o que impede que alguém
falsifique o remetente para gerar crédito.
"""
from __future__ import annotations

import asyncio
import email
import hashlib
import html
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_TIMEOUT = 25

ASSET = "USDT"

_RECEIPT_WORDS = (
    "receiv",
    "recebid",
    "recebeu",
    "recebimento",
    "recibi",
    "recibido",
    "deposit",
    "depósito",
    "creditad",
    "credited",
)
# Só olhamos o assunto aqui: rodapés da Binance citam saques em qualquer e-mail.
_NOT_RECEIPT_SUBJECT = (
    "withdraw",
    "saque",
    "retiro",
    "you have sent",
    "você enviou",
    "has enviado",
    "login",
    "verification",
    "verificação",
    "verificación",
)

_AMOUNT_AFTER = re.compile(r"([0-9][0-9.,]*)\s*" + ASSET + r"\b", re.IGNORECASE)
_AMOUNT_BEFORE = re.compile(r"\b" + ASSET + r"\s*[:\-]?\s*([0-9][0-9.,]*)", re.IGNORECASE)
_PAYER = re.compile(
    r"(?:\bfrom\b|\bsender\b|\bpayer\b|\bremetente\b|\bpagador\b|\benviado por\b)"
    r"\s*[:\-]?\s*([^\n\r<|]{2,60})",
    re.IGNORECASE,
)


class BinanceMailError(Exception):
    """Falha de conexão ou login, com mensagem pronta para o dono."""


@dataclass
class BinanceMail:
    key: str
    subject: str
    from_addr: str
    date_ts: int
    authentic: bool
    is_receipt: bool
    amount: Decimal | None
    payer: str
    text: str


@dataclass
class InboxResult:
    total_messages: int
    mails: list[BinanceMail]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _is_binance_domain(domain: str) -> bool:
    domain = (domain or "").strip().lower().rstrip(".>")
    return domain == "binance.com" or domain.endswith(".binance.com")


def _authentic(msg: Message, from_addr: str) -> bool:
    """Confere o resultado de autenticação que o Gmail grava no topo do e-mail."""
    if not _is_binance_domain(from_addr.rpartition("@")[2]):
        return False
    # O Gmail insere o próprio cabeçalho acima de todos os outros; um cabeçalho
    # forjado pelo remetente fica abaixo e é ignorado.
    results = msg.get_all("Authentication-Results") or []
    if not results:
        return False
    top = " ".join(str(results[0]).split()).lower()
    if not top.startswith("mx.google.com"):
        return False
    for match in re.finditer(r"dkim=pass[^;]*", top):
        dom = re.search(r"header\.(?:i|d)=@?([a-z0-9.\-]+)", match.group(0))
        if dom and _is_binance_domain(dom.group(1)):
            return True
    dmarc = re.search(r"dmarc=pass[^;]*", top)
    if dmarc:
        dom = re.search(r"header\.from=([a-z0-9.\-]+)", dmarc.group(0))
        if dom and _is_binance_domain(dom.group(1)):
            return True
    return False


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>|</h\d>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def _body_text(msg: Message) -> str:
    plain, rich = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in {"text/plain", "text/html"}:
            continue
        if str(part.get("Content-Disposition") or "").lower().startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
        except Exception:  # noqa: BLE001
            continue
        (plain if ctype == "text/plain" else rich).append(content)
    text = "\n".join(plain) if plain else _html_to_text("\n".join(rich))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_amount(raw: str) -> Decimal | None:
    """Aceita 10, 10.5, 10,50, 1,234.56 e 1.234,56."""
    s = (raw or "").strip().strip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = s.replace(",", "") if len(tail) == 3 and "," not in head else s.replace(",", ".")
    try:
        value = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if value <= 0:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _extract_amount(subject: str, text: str) -> Decimal | None:
    for source in (subject, text):
        for pattern in (_AMOUNT_AFTER, _AMOUNT_BEFORE):
            match = pattern.search(source)
            if match:
                value = parse_amount(match.group(1))
                if value is not None:
                    return value
    return None


def _extract_payer(text: str) -> str:
    match = _PAYER.search(text)
    if not match:
        return ""
    return match.group(1).strip(" .,:;")[:60]


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def parse_message(raw: bytes) -> BinanceMail:
    msg = email.message_from_bytes(raw)
    subject = _decode(msg.get("Subject"))
    from_addr = parseaddr(_decode(msg.get("From")))[1].lower()
    message_id = (msg.get("Message-ID") or "").strip()
    key = message_id or "sha1:" + hashlib.sha1(raw).hexdigest()

    date_ts = int(time.time())
    try:
        parsed = parsedate_to_datetime(msg.get("Date"))
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            date_ts = int(parsed.timestamp())
    except Exception:  # noqa: BLE001
        pass

    text = _body_text(msg)
    lowered_subject = subject.lower()
    haystack = f"{lowered_subject}\n{text.lower()}"
    is_receipt = any(w in haystack for w in _RECEIPT_WORDS) and not any(
        w in lowered_subject for w in _NOT_RECEIPT_SUBJECT
    )
    return BinanceMail(
        key=key,
        subject=subject,
        from_addr=from_addr,
        date_ts=date_ts,
        authentic=_authentic(msg, from_addr),
        is_receipt=is_receipt,
        amount=_extract_amount(subject, text),
        payer=_extract_payer(text),
        text=text,
    )


def _fetch_sync(address: str, password: str, since_days: int, limit: int) -> InboxResult:
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise BinanceMailError(f"Não consegui conectar ao Gmail: {exc}") from exc
    try:
        try:
            conn.login(address, password)
        except imaplib.IMAP4.error as exc:
            raise BinanceMailError(
                "O Gmail recusou o login. Confira o e-mail e a senha de app "
                "(16 letras, gerada em Conta Google > Segurança > Senhas de app)."
            ) from exc

        status, data = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise BinanceMailError("Não consegui abrir a caixa de entrada.")
        total = int(data[0] or 0) if data and data[0] else 0

        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", since, "FROM", "binance")
        if status != "OK":
            raise BinanceMailError("A busca na caixa de entrada falhou.")
        ids = (data[0] or b"").split()[-limit:]

        mails: list[BinanceMail] = []
        for msg_id in ids:
            status, parts = conn.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not parts:
                continue
            raw = next(
                (p[1] for p in parts if isinstance(p, tuple) and len(p) > 1),
                None,
            )
            if not raw:
                continue
            try:
                mails.append(parse_message(raw))
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao interpretar e-mail da Binance id=%s", msg_id)
        mails.sort(key=lambda m: m.date_ts)
        return InboxResult(total_messages=total, mails=mails)
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


async def fetch_binance_mails(
    address: str,
    password: str,
    *,
    since_days: int = 2,
    limit: int = 40,
) -> InboxResult:
    return await asyncio.to_thread(_fetch_sync, address, password, since_days, limit)
