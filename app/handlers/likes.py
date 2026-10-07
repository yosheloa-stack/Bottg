"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

import html
import logging
import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import texts
from app.config import Config
from app.database import Database
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.ffhub_shop import FFHubShopApi
from app.services.likes import LikesApi
from app.utils import clean_game_id

router = Router(name="likes")
logger = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}


def _valid_like_uid(game_id: str) -> bool:
    """A Likes Painel API aceita UIDs numéricos de 8 a 11 dígitos."""
    return game_id.isdigit() and 8 <= len(game_id) <= 11


async def _do_send_like(
    message: Message,
    game_id: str,
    likes_api: LikesApi,
    autosystem_likes_api: AutoSystemLikesApi,
    db: Database,
) -> None:
    status_msg = await message.reply("⏳ Enviando likes...")
    started_at = time.perf_counter()
    result = await likes_api.send_like(game_id)

    if (
        not result.ok
        and result.status in (429, 500, 503)
        and autosystem_likes_api.enabled
    ):
        logger.warning(
            "API principal indisponível/limitada para uid=%s status=%s; "
            "tentando fallback Auto System",
            game_id,
            result.status,
        )
        fallback_result = await autosystem_likes_api.send_like(game_id)
        if fallback_result.ok:
            logger.info(
                "Fallback Auto System entregou uid=%s likes=%s",
                game_id,
                fallback_result.data.get("likes_sent"),
            )
            result = fallback_result
        else:
            logger.warning(
                "Fallback Auto System falhou uid=%s status=%s error=%s",
                game_id,
                fallback_result.status,
                fallback_result.error,
            )
            result = fallback_result
    data = result.data if isinstance(result.data, dict) else {}
    error = (result.error or "").strip()

    if result.ok:
        sent = data.get("likes_sent")
        if sent is None:
            sent = 0

        try:
            sent_int = int(sent)
        except (TypeError, ValueError):
            sent_int = 0

        if sent_int > 0:
            await db.complete_like_slot(message.from_user.id)
        else:
            await db.release_like_slot(message.from_user.id)

        nick = data.get("nickname") or "Jogador"
        before = data.get("likes_before")
        after = data.get("likes_after")
        region = data.get("region") or "BR"
        target = data.get("target") or sent
        elapsed = time.perf_counter() - started_at

        await status_msg.edit_text(
            texts.LIKE_SUCCESS.format(
                game_id=game_id,
                nick=html.escape(str(nick)),
                region=html.escape(str(region)),
                antes=html.escape(str(before)) if before is not None else "—",
                depois=html.escape(str(after)) if after is not None else "—",
                enviadas=sent,
                target=target,
                tempo=f"{elapsed:.2f}",
            )
        )
        return

    await db.release_like_slot(message.from_user.id)

    logger.warning(
        "Falha no envio de likes uid=%s status=%s error=%s data=%s",
        game_id,
        result.status,
        error,
        data,
    )

    if result.status == 400:
        await status_msg.edit_text(
            f"❌ {html.escape(error or 'Parâmetros inválidos para envio de likes.')}"
        )
        return

    if result.status in (401, 403):
        await status_msg.edit_text(
            "⚠️ O serviço de likes está temporariamente indisponível. "
            "Avise um administrador."
        )
        return

    if result.status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return

    if result.status == 409:
        cooldown_text = data.get("tempo_restante") or ""
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(
                game_id=game_id,
                cooldown=(f"⏱ {html.escape(str(cooldown_text))}" if cooldown_text else ""),
            )
        )
        return

    if result.status == 429:
        normalized = error.lower()
        if "quota" in normalized or "diári" in normalized or "diari" in normalized:
            await status_msg.edit_text(
                f"⚠️ {html.escape(error or 'Limite diário de likes atingido.')}"
            )
        else:
            await status_msg.edit_text(
                f"⏳ {html.escape(error or 'Limite de requisições por minuto atingido. Tente novamente em instantes.')}"
            )
        return

    if result.status == 503:
        await status_msg.edit_text(
            f"⏳ {html.escape(error or 'Serviço de likes temporariamente indisponível. Tente novamente em instantes.')}"
        )
        return

    if error:
        await status_msg.edit_text(f"❌ {html.escape(error)}")
        return

    await status_msg.edit_text(texts.GENERIC_ERROR)


@router.message(Command("like", "likes"))
async def cmd_like(
    message: Message,
    command: CommandObject,
    likes_api: LikesApi,
    autosystem_likes_api: AutoSystemLikesApi,
    db: Database,
) -> None:
    # Bloqueia no privado — likes só em grupos
    if message.chat.type not in GROUP_TYPES:
        await message.answer(texts.LIKE_PRIVATE_BLOCKED)
        return

    arg = (command.args or "").strip()
    if not arg:
        await message.reply("Use assim: <code>/like SEU_ID</code>")
        return

    game_id = clean_game_id(arg.split()[0])
    if not _valid_like_uid(game_id):
        await message.reply(
            "⚠️ ID inválido. Envie somente números, de 8 a 11 dígitos."
        )
        return

    allowed, remaining = await db.acquire_like_slot(message.from_user.id)
    if not allowed:
        hours, rem = divmod(max(0, remaining), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours > 0:
            wait_text = f"{hours}h {minutes}min"
        elif minutes > 0:
            wait_text = f"{minutes}min {seconds}s"
        else:
            wait_text = f"{seconds}s"

        await message.reply(
            "⏳ <b>Limite de envio atingido.</b>\n\n"
            "Cada pessoa pode fazer <b>1 envio de likes a cada 24 horas</b>.\n"
            f"Você poderá usar novamente em <b>{wait_text}</b>."
        )
        return

    try:
        await _do_send_like(
            message,
            game_id,
            likes_api,
            autosystem_likes_api,
            db,
        )
    except Exception:
        await db.release_like_slot(message.from_user.id)
        raise


@router.message(Command("like2"))
async def cmd_like2(
    message: Message,
    command: CommandObject,
    config: Config,
    ffhub_shop: FFHubShopApi,
) -> None:
    """Envio pago de likes pela FFHub Shop. Separado do /like grátis."""
    if not message.from_user or message.from_user.id not in config.admin_ids:
        await message.reply(
            "🔒 <b>O /like2 é um serviço pago.</b>\n"
            "Este comando fica disponível somente para o dono/admin."
        )
        return

    arg = (command.args or "").strip()
    if not arg:
        await message.reply("Use assim: <code>/like2 SEU_ID</code>")
        return

    game_id = clean_game_id(arg.split()[0])
    if not _valid_like_uid(game_id):
        await message.reply(
            "⚠️ ID inválido. Envie somente números, de 8 a 11 dígitos."
        )
        return

    if not ffhub_shop.configured:
        await message.reply(
            "⚠️ A API paga FFHub ainda não está configurada. "
            "Adicione <code>FFHUB_API_KEY</code> nas variáveis."
        )
        return

    status_msg = await message.reply("💳 Enviando likes pagos...")
    started_at = time.perf_counter()
    result = await ffhub_shop.send_paid_likes(game_id)
    data = result.data if isinstance(result.data, dict) else {}

    if not result.ok:
        error = (
            result.error
            or data.get("erro")
            or data.get("error")
            or data.get("mensagem")
            or data.get("message")
            or "A FFHub não conseguiu concluir o envio."
        )
        logger.warning(
            "Falha /like2 FFHub uid=%s status=%s error=%s data=%s",
            game_id,
            result.status,
            error,
            data,
        )
        await status_msg.edit_text(
            f"❌ <b>LIKE2 não enviado</b>\n\n"
            f"🆔 UID: <code>{game_id}</code>\n"
            f"⚠️ {html.escape(str(error))}"
        )
        return

    nick = (
        data.get("nickname")
        or data.get("nick")
        or data.get("player_name")
        or "Jogador"
    )
    sent = (
        data.get("likes_enviados")
        or data.get("likes_sent")
        or data.get("enviados")
        or data.get("likes")
        or "—"
    )
    before = data.get("likes_antes") or data.get("likes_before") or data.get("before")
    after = data.get("likes_depois") or data.get("likes_after") or data.get("after")
    balance = data.get("balance")
    if balance is None:
        balance = data.get("saldo")
    elapsed = time.perf_counter() - started_at

    lines = [
        "💎 <b>LIKE2 ENVIADO</b>",
        "",
        f"👤 Jogador: <b>{html.escape(str(nick))}</b>",
        f"🆔 UID: <code>{game_id}</code>",
    ]
    if before is not None:
        lines.append(f"📊 Likes antes: <b>{html.escape(str(before))}</b>")
    if after is not None:
        lines.append(f"📈 Likes agora: <b>{html.escape(str(after))}</b>")
    lines.append(f"❤️ Enviados: <b>+{html.escape(str(sent))}</b>")
    if balance is not None:
        lines.append(f"💰 Saldo API: <b>{html.escape(str(balance))}</b>")
    lines.extend(
        [
            f"⚡ Tempo: <b>{elapsed:.2f}s</b>",
            "",
            "✅ Envio pago processado pela FFHub.",
        ]
    )

    await status_msg.edit_text("\n".join(lines))
