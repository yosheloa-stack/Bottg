"""Comando de Auto Like FFHub exclusivo dos donos."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.config import Config
from app.database import Database
from app.services.ffhub_shop import FFHubShopApi
from app.utils import clean_game_id

router = Router(name="owner_autolike")


def _valid_uid(uid: str) -> bool:
    return uid.isdigit() and 8 <= len(uid) <= 11


@router.message(Command("autolike"))
async def cmd_owner_autolike(
    message: Message,
    command: CommandObject,
    config: Config,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    if not message.from_user:
        return

    # Exclusivo dos donos, tanto no privado quanto em grupos.
    if message.from_user.id not in config.admin_ids:
        await message.reply(
            "🔒 <b>Comando exclusivo dos donos.</b>"
        )
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
            "<code>/autolike cancel UID</code>\n\n"
            "✅ 1 envio por dia pela FFHub\n"
            "✅ aproximadamente 500 a 1.000 likes por envio"
        )
        return

    action = args[0].lower()

    if action in {"list", "lista"}:
        subs = await db.active_ffhub_autolikes(limit=50)
        if not subs:
            await message.reply("🔥 <b>Auto Likes ativos</b>\n\nNenhum ativo.")
            return

        lines = ["🔥 <b>AUTO LIKES ATIVOS</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for sub in subs:
            done = int(sub.get("sends_done") or 0)
            total = int(sub.get("days_total") or 0)
            lines.append(
                f"• <code>{sub['game_id']}</code> — "
                f"<b>{done}/{total}</b> envios"
            )
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
        user_id=message.from_user.id,
        game_id=uid,
        days_total=days,
    )

    if not created:
        await message.reply(
            "ℹ️ <b>Esse UID já possui Auto Like ativo.</b>\n\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"📄 Assinatura: <code>#{sub_id}</code>"
        )
        return

    await message.reply(
        "🔥 <b>AUTO LIKE 500/1000 ATIVADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 UID: <code>{uid}</code>\n"
        f"📆 Duração: <b>{days} dias</b>\n"
        "❤️ Envio: <b>aprox. 500 a 1.000 likes/dia</b>\n"
        "🔁 Frequência: <b>1 envio por dia</b>\n\n"
        "⚡ O primeiro envio já entrou na fila."
    )
