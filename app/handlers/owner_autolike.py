"""Comando de Auto Like FFHub exclusivo dos donos."""
from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.config import Config
from app.database import Database
from app.owner_auth import resolve_owner_id
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
from app.utils import clean_game_id

router = Router(name="owner_autolike")


TZ_BR = ZoneInfo("America/Sao_Paulo")


def _valid_uid(uid: str) -> bool:
    return uid.isdigit() and 8 <= len(uid) <= 11


def _to_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip().replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fmt_time(timestamp: int | None) -> str:
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(int(timestamp), TZ_BR).strftime("%d/%m/%Y às %H:%M")


@router.message(Command("autolike"))
async def cmd_owner_autolike(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    ffhub_shop: FFHubShopApi,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        await message.reply("🔒 <b>Comando exclusivo dos donos.</b>")
        return

    args = (command.args or "").split()
    if not args:
        await message.reply(
            "🔥 <b>AUTO LIKE 500/1000 • DONO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Ativar:\n"
            "<code>/autolike UID DIAS</code>\n\n"
            "Exemplo:\n"
            "<code>/autolike 813856263 7</code>\n\n"
            "Gerenciar:\n"
            "<code>/autolike list</code>\n"
            "<code>/autolike status UID</code>\n"
            "<code>/autolike cancel UID</code>\n\n"
            "✅ 1 envio por dia pela FFHub\n"
            "✅ aproximadamente 500 a 1.000 likes por envio"
        )
        return

    action = args[0].lower()

    if action in {"list", "lista"}:
        subs = await db.active_ffhub_autolikes_with_stats(limit=50)
        if not subs:
            await message.reply("🔥 <b>Auto Likes ativos</b>\n\nNenhum ativo.")
            return

        lines = ["🔥 <b>AUTO LIKES ATIVOS</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for sub in subs:
            done = int(sub.get("sends_done") or 0)
            total = int(sub.get("days_total") or 0)
            total_sent = int(sub.get("total_likes_sent") or 0)
            last_sent = sub.get("last_likes_sent")
            nick = html.escape(str(sub.get("nickname") or "Jogador"))
            lines.extend(
                [
                    f"👤 <b>{nick}</b>",
                    f"🆔 <code>{sub['game_id']}</code>",
                    f"📆 Envios: <b>{done}/{total}</b>",
                    f"❤️ Total enviado: <b>+{total_sent}</b>",
                    f"📈 Último envio: <b>+{int(last_sent or 0)}</b>",
                    f"⏰ Próximo: <b>{_fmt_time(sub.get('next_send_at'))}</b>",
                    "",
                ]
            )
        await message.reply("\n".join(lines).rstrip())
        return

    if action in {"status", "ver"}:
        if len(args) < 2:
            await message.reply("Use: <code>/autolike status UID</code>")
            return

        uid = clean_game_id(args[1])
        if not _valid_uid(uid):
            await message.reply("⚠️ UID inválido.")
            return

        sub = await db.get_ffhub_autolike_status(uid)
        if not sub:
            await message.reply("ℹ️ Nenhum Auto Like encontrado para esse UID.")
            return

        done = int(sub.get("sends_done") or 0)
        total = int(sub.get("days_total") or 0)
        remaining = max(0, total - done)
        total_sent = int(sub.get("total_likes_sent") or 0)
        last_sent = int(sub.get("last_likes_sent") or 0)
        nick = html.escape(str(sub.get("nickname") or "Jogador"))
        status_label = {
            "active": "🟢 Ativo",
            "completed": "✅ Concluído",
            "cancelled": "⛔ Cancelado",
        }.get(str(sub.get("status")), str(sub.get("status") or "—"))

        lines = [
            "🔥 <b>STATUS DO AUTO LIKE</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 Nick: <b>{nick}</b>",
            f"🆔 UID: <code>{uid}</code>",
            f"📌 Status: <b>{status_label}</b>",
            f"📆 Envios: <b>{done}/{total}</b>",
            f"⌛ Restantes: <b>{remaining}</b>",
            f"❤️ Total enviado: <b>+{total_sent}</b>",
            f"📈 Último envio: <b>+{last_sent}</b>",
        ]
        if sub.get("last_likes_before") is not None:
            lines.append(f"📊 Likes antes: <b>{sub['last_likes_before']}</b>")
        if sub.get("last_likes_after") is not None:
            lines.append(f"📊 Likes depois: <b>{sub['last_likes_after']}</b>")
        if sub.get("status") == "active":
            lines.append(
                f"⏰ Próximo envio: <b>{_fmt_time(sub.get('next_send_at'))}</b>"
            )
        lines.append(f"🕒 Cadastrado: <b>{_fmt_time(sub.get('created_at'))}</b>")

        await message.reply("\n".join(lines))
        return

    if action in {"cancel", "cancelar", "del", "remove"}:
        if len(args) < 2:
            await message.reply(
                "Use: <code>/autolike cancel UID</code>"
            )
            return
        uid = clean_game_id(args[1])
        if not _valid_uid(uid):
            await message.reply("⚠️ UID inválido.")
            return

        cancelled = await db.cancel_ffhub_autolike_by_uid(uid)
        if cancelled:
            await message.reply(
                "✅ <b>Auto Like cancelado</b>\n\n"
                f"🆔 UID: <code>{uid}</code>\n"
                f"📦 Assinaturas encerradas: <b>{cancelled}</b>"
            )
        else:
            await message.reply(
                "ℹ️ Não existe Auto Like ativo para esse UID."
            )
        return

    uid = clean_game_id(args[0])
    if not _valid_uid(uid):
        await message.reply(
            "⚠️ UID inválido.\n"
            "Use: <code>/autolike UID DIAS</code>"
        )
        return

    if len(args) < 2:
        await message.reply(
            "⚠️ Informe a quantidade de dias.\n"
            "Exemplo: <code>/autolike 813856263 7</code>"
        )
        return

    try:
        days = int(args[1])
    except ValueError:
        await message.reply("⚠️ A quantidade de dias precisa ser um número.")
        return

    if not 1 <= days <= 365:
        await message.reply("⚠️ Use entre <b>1 e 365 dias</b>.")
        return

    if not ffhub_shop.configured:
        await message.reply(
            "⚠️ <b>FFHub não configurada.</b>\n"
            "Configure a FFHub no painel do dono antes de ativar."
        )
        return

    sub_id, created = await db.create_owner_ffhub_autolike_subscription(
        user_id=owner_id,
        game_id=uid,
        days_total=days,
    )

    if not created:
        await message.reply(
            "ℹ️ <b>Esse UID já possui Auto Like ativo.</b>\n\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"📄 Assinatura: <code>#{sub_id}</code>\n\n"
            f"Use <code>/autolike status {uid}</code> para consultar."
        )
        return

    sending = await message.reply(
        "🔥 <b>Auto Like cadastrado.</b>\n"
        "Fazendo o primeiro envio agora..."
    )
    result = await ffhub_shop.send_paid_likes(uid)

    if not result.ok:
        await db.retry_ffhub_autolike_later(
            sub_id,
            result_text=str(result.data or result.error or ""),
            delay_seconds=900,
        )
        await sending.edit_text(
            "⚠️ <b>AUTO LIKE CADASTRADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"📆 Duração: <b>{days} dias</b>\n\n"
            "O primeiro envio não foi confirmado agora.\n"
            "🔁 O sistema vai tentar novamente em cerca de <b>15 minutos</b>."
        )
        return

    data = result.data if isinstance(result.data, dict) else {}
    parsed = parse_like_delivery(data)
    sent = _to_int(parsed.get("sent")) or 0
    before = _to_int(parsed.get("before"))
    after = _to_int(parsed.get("after"))
    nick = str(parsed.get("nickname") or "Jogador")

    updated = await db.complete_ffhub_autolike_send(
        sub_id,
        result_text=str(data),
        nickname=nick,
        likes_before=before,
        likes_after=after,
        likes_sent=sent,
    )
    sub = await db.get_ffhub_autolike_status(uid)

    if not updated or not sub:
        await sending.edit_text(
            "⚠️ O envio foi feito, mas não consegui carregar o status da assinatura."
        )
        return

    next_text = (
        _fmt_time(sub.get("next_send_at"))
        if sub.get("status") == "active"
        else "Plano concluído"
    )
    await sending.edit_text(
        "🔥 <b>AUTO LIKE 500/1000 ATIVADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Nick: <b>{html.escape(nick)}</b>\n"
        f"🆔 UID: <code>{uid}</code>\n"
        f"📆 Duração: <b>{days} dias</b>\n"
        f"❤️ Primeiro envio: <b>+{sent}</b>\n"
        f"📊 Likes antes: <b>{before if before is not None else '—'}</b>\n"
        f"📊 Likes depois: <b>{after if after is not None else '—'}</b>\n"
        f"❤️ Total acumulado: <b>+{int(sub.get('total_likes_sent') or 0)}</b>\n"
        f"⏰ Próximo envio: <b>{next_text}</b>\n\n"
        "✅ Os próximos envios seguirão o mesmo horário do cadastro."
    )
