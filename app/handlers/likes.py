"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

import asyncio
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
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
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
    db: Database,
    config: Config,
    ffhub_shop: FFHubShopApi,
) -> None:
    """Dono envia direto; clientes recebem os pacotes pagos."""
    if not message.from_user:
        return

    is_owner = message.from_user.id in config.admin_ids
    arg = (command.args or "").strip()
    anonymous_admin = (
        message.chat.type in GROUP_TYPES
        and message.sender_chat is not None
    )

    # Se foi usado /like2 UID, nunca abre os pacotes.
    # Em modo "Envio anônimo" o Telegram oculta o ID real do administrador.
    if arg and not is_owner:
        if anonymous_admin:
            await message.reply(
                "⚠️ <b>Você está enviando como administrador anônimo.</b>\n\n"
                "O Telegram esconde seu ID real nesse modo, então eu não consigo "
                "confirmar que você é um dos donos.\n\n"
                "Desative <b>Envio anônimo</b> nesse grupo e mande novamente:\n"
                "<code>/like2 SEU_ID</code>\n\n"
                "Ou use o comando no privado do bot."
            )
        else:
            await message.reply(
                "🔒 <b>Esse comando direto é exclusivo dos donos.</b>\n\n"
                f"Seu ID detectado: <code>{message.from_user.id}</code>\n"
                "Para comprar, use <code>/like2</code> sem informar UID."
            )
        return

    # Donos usam /like2 UID para envio direto, sem pagamento.
    if is_owner:
        if not arg:
            await message.reply(
                "💎 <b>LIKE2 • ENVIO DIRETO</b>\n\n"
                "Use assim:\n"
                "<code>/like2 SEU_ID</code>\n\n"
                "Esse comando envia direto pela FFHub, sem PIX e sem pacote."
            )
            return

        game_id = clean_game_id(arg.split()[0])
        if not _valid_like_uid(game_id):
            await message.reply(
                "⚠️ ID inválido. Envie somente números, de 8 a 11 dígitos.\n"
                "Exemplo: <code>/like2 813856263</code>"
            )
            return

        if not ffhub_shop.configured:
            await message.reply(
                "⚠️ <b>FFHub não configurada.</b>\n\n"
                "Abra <b>Painel administrativo → Configurações do dono → FFHub API Key</b>."
            )
            return

        status_msg = await message.reply("💎 Enviando Like2...")
        started_at = time.perf_counter()
        logger.info(
            "LIKE2 inicio | owner=%s uid=%s",
            message.from_user.id,
            game_id,
        )
        try:
            result = await asyncio.wait_for(
                ffhub_shop.send_paid_likes(game_id),
                timeout=60,
            )
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - started_at
            logger.error(
                "LIKE2 timeout externo | owner=%s uid=%s elapsed=%.2fs",
                message.from_user.id,
                game_id,
                elapsed,
            )
            await status_msg.edit_text(
                "⏱️ <b>LIKE2 sem resposta da FFHub</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                "A API não respondeu em até <b>60 segundos</b>.\n\n"
                "⚠️ Não vou repetir automaticamente para evitar gastar saldo/enviar duas vezes."
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado no /like2 uid=%s", game_id)
            await status_msg.edit_text(
                "❌ <b>Erro no LIKE2</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                f"⚠️ {html.escape(str(exc) or 'Falha inesperada ao chamar a FFHub.')}"
            )
            return

        logger.info(
            "LIKE2 fim | owner=%s uid=%s status=%s ok=%s elapsed=%.2fs",
            message.from_user.id,
            game_id,
            result.status,
            result.ok,
            time.perf_counter() - started_at,
        )

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
            await status_msg.edit_text(
                "❌ <b>LIKE2 não enviado</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                f"⚠️ {html.escape(str(error))}"
            )
            return

        parsed_like = parse_like_delivery(data)
        nick = parsed_like["nickname"]
        sent = parsed_like["sent"]
        before = parsed_like["before"]
        after = parsed_like["after"]
        sent_display = sent if sent is not None else "—"
        elapsed = time.perf_counter() - started_at

        lines = [
            "💚 <b>LIKE2 ENVIADO</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 Jogador: <b>{html.escape(str(nick))}</b>",
            f"🆔 UID: <code>{game_id}</code>",
        ]
        if before is not None:
            lines.append(f"📊 Likes antes: <b>{html.escape(str(before))}</b>")
        if after is not None:
            lines.append(f"📈 Likes agora: <b>{html.escape(str(after))}</b>")
        lines.extend(
            [
                f"❤️ Enviados: <b>+{html.escape(str(sent_display))}</b>",
                f"⚡ Tempo: <b>{elapsed:.2f}s</b>",
                "",
                "✅ Envio direto concluído.",
            ]
        )
        await status_msg.edit_text("\n".join(lines))
        return

    # Clientes usam /like2 apenas para abrir os pacotes pagos.
    products = await resolve_products(config, db)
    ffhub_private_key = await db.get_owner_setting("FFHUB_API_KEY")
    ffhub_configured = bool(ffhub_private_key or config.ffhub_api_key)

    await message.answer(
        "💎 <b>LIKE2 PREMIUM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Escolha <b>envio único</b> ou um plano automático.\n\n"
        "✅ Envio único\n"
        "✅ Auto-Like de 7, 15 ou 30 dias\n"
        "✅ Pagamento via PIX\n"
        "✅ UID solicitado somente após o pagamento\n\n"
        "👇 Escolha uma opção:",
        reply_markup=premium_like_menu(
            products,
            is_owner=False,
            ffhub_configured=ffhub_configured,
        ),
    )

