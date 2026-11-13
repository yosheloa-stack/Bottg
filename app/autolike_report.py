"""Layout estilizado do Auto Like 220 e relatório diário de envios.

Emojis animados (custom emoji): o Telegram só renderiza custom emoji enviados
por bots que tenham um username colecionável comprado no Fragment. O bot já
fica preparado: o dono responde, com /emojis, a uma mensagem que contenha os
emojis premium desejados; cada emoji comum dos layouts passa a ser trocado
pela versão animada correspondente. Se o Telegram recusar, a mensagem é
reenviada com os emojis normais.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

from app.announcements import _acquire_markup, _target, _user_label
from app.config import Config
from app.database import Database
from app.database.db import BRAZIL_TZ
from app.outgoing import keep_raw_output, reset_raw_output
from app.owner_auth import resolve_owner_id

logger = logging.getLogger(__name__)
router = Router(name="autolike_report")

EMOJI_SETTING = "CUSTOM_EMOJI_MAP"
REPORT_SETTING = "AUTOLIKE_REPORT_LAST_DATE"
REPORT_HOUR, REPORT_MINUTE = 23, 55
LINE = "➿" * 16

# Regiões (servidores do Free Fire) oferecidas no cadastro do ID, em ordem.
REGIONS: dict[str, str] = {
    "BR": "🇧🇷", "IND": "🇮🇳", "US": "🇺🇸", "NA": "🇺🇸", "SAC": "🌎",
    "ME": "🇸🇦", "ID": "🇮🇩", "TH": "🇹🇭", "VN": "🇻🇳",
    "SG": "🇸🇬", "PK": "🇵🇰", "BD": "🇧🇩", "TW": "🇹🇼",
    "RU": "🇷🇺", "EU": "🇪🇺",
}
_FLAGS = {**REGIONS, "IN": "🇮🇳", "SA": "🇸🇦", "LATAM": "🌎"}


def region_flag(region: str | None) -> str:
    return _FLAGS.get((region or "BR").upper(), "🌐")


# --------------------------------------------------------------- texto bold
def bold(text: str) -> str:
    """Converte A-Z, a-z e 0-9 para negrito Unicode (𝗔𝗕𝗖 𝟭𝟮𝟯)."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D5EE + ord(ch) - ord("a")))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7EC + ord(ch) - ord("0")))
        else:
            out.append(ch)
    return "".join(out)


def _fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def progress_bar(done: int, total: int, size: int = 10) -> str:
    pct = 100 if total <= 0 else max(0, min(100, round(done * 100 / total)))
    filled = round(pct * size / 100)
    return f"{'▰' * filled}{'▱' * (size - filled)} {pct}%"


# ------------------------------------------------------------ custom emoji
def _norm(s: str) -> str:
    return s.replace("️", "")


async def _emoji_map(db: Database) -> dict[str, str]:
    raw = await db.get_owner_setting(EMOJI_SETTING)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except ValueError:
        return {}


def apply_custom_emoji(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    keys = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(
        "|".join("️?".join(map(re.escape, k)) + "️?" for k in keys)
    )
    # Não troca dentro de tags HTML (atributos, etc.).
    parts = re.split(r"(<[^>]+>)", text)
    for i, part in enumerate(parts):
        if part.startswith("<"):
            continue
        parts[i] = pattern.sub(
            lambda m: (
                f'<tg-emoji emoji-id="{mapping[_norm(m.group(0))]}">'
                f"{m.group(0)}</tg-emoji>"
            ),
            part,
        )
    return "".join(parts)


async def edit_fancy(target: Message, db: Database, text: str, **kwargs) -> None:
    """Edita uma mensagem com o layout (mantém emojis; custom emoji se houver)."""
    mapping = await _emoji_map(db)
    token = keep_raw_output()
    try:
        if mapping:
            try:
                await target.edit_text(apply_custom_emoji(text, mapping), **kwargs)
                return
            except TelegramBadRequest as exc:
                logger.warning("Custom emoji recusado (%s); usando emojis normais.", exc)
        await target.edit_text(text, **kwargs)
    finally:
        reset_raw_output(token)


async def send_fancy(bot: Bot, db: Database, chat_id: int, text: str, **kwargs) -> None:
    """Envia com custom emoji (se configurado); se falhar, usa emojis normais."""
    mapping = await _emoji_map(db)
    # O filtro de saída do bot remove emojis; este layout precisa deles.
    token = keep_raw_output()
    try:
        if mapping:
            try:
                await bot.send_message(
                    chat_id, apply_custom_emoji(text, mapping), **kwargs
                )
                return
            except TelegramBadRequest as exc:
                logger.warning("Custom emoji recusado (%s); usando emojis normais.", exc)
        await bot.send_message(chat_id, text, **kwargs)
    finally:
        reset_raw_output(token)


@router.message(Command("emojis"))
async def cmd_emojis(message: Message, bot: Bot, config: Config, db: Database) -> None:
    if await resolve_owner_id(message, bot, config) is None:
        return
    arg = (message.text or "").split(maxsplit=1)
    if len(arg) > 1 and arg[1].strip().lower() in {"limpar", "reset", "off"}:
        await db.set_owner_setting(EMOJI_SETTING, "{}")
        await message.reply("🧹 Emojis animados removidos. Layouts voltam aos emojis normais.")
        return

    src = message.reply_to_message
    entities = (src.entities or src.caption_entities or []) if src else []
    body = (src.text or src.caption or "") if src else ""
    found: dict[str, str] = {}
    for ent in entities:
        if ent.type == "custom_emoji" and ent.custom_emoji_id:
            found[_norm(ent.extract_from(body))] = ent.custom_emoji_id
    if not found:
        mapping = await _emoji_map(db)
        await message.reply(
            "✨ <b>Emojis animados dos layouts</b>\n\n"
            "Responda com <code>/emojis</code> a uma mensagem que tenha os emojis "
            "premium que você quer usar (ex.: 🎉 ✨ 🏆 🎁 👤 🆔 ⚙️ ♥️ 📉 📈 🚀 ⏰ ➿). "
            "Cada um substitui o emoji comum igual nos layouts.\n"
            "<code>/emojis limpar</code> remove tudo.\n\n"
            f"Configurados agora: <b>{len(mapping)}</b>\n\n"
            "⚠️ O Telegram só mostra emoji animado enviado por bot se o bot tiver "
            "um username colecionável comprado no Fragment. Sem isso, aparecem os "
            "emojis normais."
        )
        return
    mapping = await _emoji_map(db)
    mapping.update(found)
    await db.set_owner_setting(EMOJI_SETTING, json.dumps(mapping, ensure_ascii=False))
    await message.reply(
        f"✅ <b>{len(found)}</b> emoji(s) salvos: {' '.join(found)}\n"
        f"Total configurado: <b>{len(mapping)}</b>"
    )


# ------------------------------------------------- layout Auto Like 220/500
def autolike_layout_text(
    *,
    plan: str = "220 LIKE",
    title: str = "AUTOLIKE SEND SUCCESS",
    nickname: str,
    uid: str,
    region: str,
    sent: int,
    before: int | None,
    after: int | None,
    done: int,
    total: int,
    client_label: str | None = None,
    accumulated: int | None = None,
    elapsed: float | None = None,
) -> str:
    now = datetime.now(BRAZIL_TZ)
    region = (region or "BR").upper()
    flag = region_flag(region)
    lines = [
        LINE,
        f"✨ 🎉 <b>{bold(title)}</b> 🎉 ✨",
        f"🏆 <b>{bold('FULL POWER DELIVERY')}</b> 🏆",
        f"🎁 {bold('PLAN')} ➜ <b>{bold(plan)}</b>",
    ]
    if client_label:
        lines.append(f"🛒 {bold('CLIENTE')} {client_label}")
    lines += [
        f"👤 {bold('NAME')} ㅤ<b>{html.escape(nickname or 'Jogador')}</b>",
        f"🆔 {bold('UID')} <code>{html.escape(str(uid))}</code>",
        f"⚙️ {bold('SERVER')} {flag} {html.escape(region)}",
        f"♥️ {bold('LIKES GIVEN')} <b>+{_fmt(sent)}</b>",
    ]
    if before is not None:
        lines.append(f"📉 {bold('BEFORE LIKES')} {_fmt(before)}")
    if after is not None:
        lines.append(f"📈 {bold('CURRENT LIKES')} {_fmt(after)}")
    if accumulated is not None:
        lines.append(f"💎 {bold('TOTAL SENT')} +{_fmt(accumulated)}")
    count = f" ({done}/{total})" if total > 1 else ""
    lines.append(f"🏆 {bold('PROGRESS')} {progress_bar(done, total)}{count}")
    if before:
        lines.append(f"🚀 {bold('GROWTH')} +{sent * 100 / before:.2f}%")
    if elapsed is not None:
        lines.append(f"⚡ {bold('SPEED')} {elapsed:.2f}s")
    lines.append(
        f"⏰ {bold('TIME (BR)')} {now.strftime('%d/%m/%Y')} • {now.strftime('%H:%M')}"
    )
    lines.append(LINE)
    return "\n".join(lines)


# Compatibilidade com o nome antigo.
autolike220_text = autolike_layout_text


async def announce_autolike_layout(
    bot: Bot, db: Database, *, user_id: int, **fields
) -> None:
    target = await _target(db)
    if not target:
        return
    chat_id, thread_id = target
    text = autolike_layout_text(client_label=await _user_label(db, user_id), **fields)
    kwargs = {"reply_markup": await _acquire_markup(bot)}
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    await send_fancy(bot, db, chat_id, text, **kwargs)


announce_autolike220 = announce_autolike_layout


# ------------------------------------------- comprovante de cadastro (ativação)
def activation_text(
    *,
    plan: str,
    uid: str,
    region: str,
    days: int,
    per_send: str,
    next_send_ts: int | None = None,
    footer: str | None = None,
) -> str:
    from app.database.db import next_autolike_send_epoch

    now = datetime.now(BRAZIL_TZ)
    region = (region or "BR").upper()
    nxt = datetime.fromtimestamp(
        int(next_send_ts or next_autolike_send_epoch()), BRAZIL_TZ
    )
    lines = [
        LINE,
        f"✨ ✅ <b>{bold('AUTOLIKE REGISTERED')}</b> ✅ ✨",
        f"🏆 <b>{bold('PLAN ACTIVATED')}</b> 🏆",
        f"🎁 {bold('PLAN')} ➜ <b>{bold(plan)}</b>",
        f"🆔 {bold('UID')} <code>{html.escape(str(uid))}</code>",
        f"⚙️ {bold('SERVER')} {region_flag(region)} {html.escape(region)}",
        f"📆 {bold('DAYS')} {int(days)}",
        f"♥️ {bold('PER SEND')} {html.escape(per_send)}",
        f"🔁 {bold('FREQUENCY')} 1x/dia",
        f"🏆 {bold('PROGRESS')} {progress_bar(0, max(1, int(days)))} (0/{int(days)})",
        f"🚀 {bold('FIRST SEND')} {nxt.strftime('%d/%m/%Y')} • {nxt.strftime('%H:%M')}",
        f"⏰ {bold('TIME (BR)')} {now.strftime('%d/%m/%Y')} • {now.strftime('%H:%M')}",
    ]
    if footer:
        lines += ["", footer]
    lines.append(LINE)
    return "\n".join(lines)


# ------------------------------------------------------------ relatório diário
def _day_bounds(day: datetime) -> tuple[int, int]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


async def build_daily_report(db: Database, day: datetime) -> str:
    start, end = _day_bounds(day)
    summary = await db.autolike_daily_summary(start, end)
    s220 = summary.get("autosystem", {"sends": 0, "uids": 0, "likes": 0})
    s500 = summary.get("ffhub", {"sends": 0, "uids": 0, "likes": 0})
    total_likes = s220["likes"] + s500["likes"]
    total_sends = s220["sends"] + s500["sends"]
    return "\n".join(
        [
            LINE,
            f"📊 <b>{bold('RELATORIO FINAL DO DIA')}</b> 📊",
            f"🤖 <b>{bold('AUTO LIKE')}</b> • {day.strftime('%d/%m/%Y')}",
            "",
            f"❤️ <b>{bold('AUTO LIKE 220')}</b>",
            f"   📦 {bold('ENVIOS')} {_fmt(s220['sends'])} ({_fmt(s220['uids'])} UIDs)",
            f"   ♥️ {bold('LIKES')} <b>+{_fmt(s220['likes'])}</b>",
            "",
            f"🔥 <b>{bold('AUTO LIKE 500/1000')}</b>",
            f"   📦 {bold('ENVIOS')} {_fmt(s500['sends'])} ({_fmt(s500['uids'])} UIDs)",
            f"   ♥️ {bold('LIKES')} <b>+{_fmt(s500['likes'])}</b>",
            "",
            f"🏆 {bold('TOTAL ENVIOS')} {_fmt(total_sends)}",
            f"🚀 {bold('TOTAL LIKES')} <b>+{_fmt(total_likes)}</b>",
            LINE,
        ]
    )


async def send_daily_report(bot: Bot, db: Database, config: Config, day: datetime) -> None:
    text = await build_daily_report(db, day)
    for admin_id in config.admin_ids:
        try:
            await send_fancy(bot, db, int(admin_id), text)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao enviar relatório diário ao dono %s", admin_id)
    target = await _target(db)
    if target:
        chat_id, thread_id = target
        kwargs = {"reply_markup": await _acquire_markup(bot)}
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id
        try:
            await send_fancy(bot, db, chat_id, text, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao enviar relatório diário no grupo %s", chat_id)


async def watch_daily_report(bot: Bot, db: Database, config: Config) -> None:
    """Envia o relatório final do dia às 23:55 (horário de Brasília)."""
    while True:
        try:
            now = datetime.now(BRAZIL_TZ)
            today = now.strftime("%Y-%m-%d")
            if (now.hour, now.minute) >= (REPORT_HOUR, REPORT_MINUTE):
                if (await db.get_owner_setting(REPORT_SETTING) or "") != today:
                    await db.set_owner_setting(REPORT_SETTING, today)
                    await send_daily_report(bot, db, config, now)
        except Exception:  # noqa: BLE001
            logger.exception("Erro no relatório diário do Auto Like")
        await asyncio.sleep(30)


@router.message(Command("relatorio"))
async def cmd_relatorio(message: Message, bot: Bot, config: Config, db: Database) -> None:
    """/relatorio (hoje) ou /relatorio ontem — só para os donos."""
    if await resolve_owner_id(message, bot, config) is None:
        await message.reply("🔒 <b>Comando exclusivo dos donos.</b>")
        return
    day = datetime.now(BRAZIL_TZ)
    if "ontem" in (message.text or "").lower():
        day -= timedelta(days=1)
    await send_fancy(bot, db, message.chat.id, await build_daily_report(db, day))
