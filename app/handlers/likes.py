"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

import html
import logging
import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import texts
from app.catalog import resolve_products
from app.config import Config
from app.database import Database
from app.keyboards.inline import premium_like_menu
from app.services.autosystem_likes import AutoSystemLikesApi
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
    db: Database,
    config: Config,
) -> None:
    """Abre os pacotes pagos de Auto-Like Premium."""
    products = await resolve_products(config, db)
    is_owner = message.from_user.id in config.admin_ids
    ffhub_private_key = await db.get_owner_setting("FFHUB_API_KEY")
    ffhub_configured = bool(ffhub_private_key or config.ffhub_api_key)

    setup_notes = []
    if is_owner:
        if not ffhub_configured:
            setup_notes.append("🔑 Falta configurar a <b>FFHub API Key</b>.")
        without_price = [rp for rp in products.values() if rp.code.startswith("like2_") and rp.price <= 0]
        if without_price:
            setup_notes.append(
                f"💰 Falta definir preço em <b>{len(without_price)} produto(s)</b>."
            )

    setup_text = ""
    if setup_notes:
        setup_text = "\n\n⚙️ <b>Configuração do dono</b>\n" + "\n".join(setup_notes)

    await message.answer(
        "💎 <b>LIKE2 PREMIUM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Escolha <b>envio único</b> ou um plano automático.\n\n"
        "✅ Envio único\n"
        "✅ Auto-Like de 7, 15 ou 30 dias\n"
        "✅ Pagamento via PIX\n"
        "✅ UID solicitado somente após o pagamento"
        f"{setup_text}\n\n"
        "👇 Escolha uma opção:",
        reply_markup=premium_like_menu(
            products,
            is_owner=is_owner,
            ffhub_configured=ffhub_configured,
        ),
    )
