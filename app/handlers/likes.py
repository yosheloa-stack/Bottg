"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import texts
from app.announcements import announce_autolike_send
from app.config import Config
from app.database import Database
from app.keyboards.inline import main_menu
from app.owner_auth import group_has_owner_admin, resolve_owner_id
from app.promo import group_store_markup
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
from app.utils import clean_game_id

router = Router(name="likes")
logger = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}

async def _delete_user_command(message: Message, bot: Bot) -> None:
    """Apaga comandos em grupos sem interromper o processamento."""
    if message.chat.type not in GROUP_TYPES:
        return
    try:
        await bot.delete_message(message.chat.id, message.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Falha ao apagar comando no grupo %s: %s", message.chat.id, exc)



def _valid_like_uid(game_id: str) -> bool:
    """Valida UID numérico aceito pelo comando /like."""
    return game_id.isdigit() and 8 <= len(game_id) <= 11


def _to_counter_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip().replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


async def _do_send_like(
    message: Message,
    game_id: str,
    autosystem_likes_api: AutoSystemLikesApi,
    db: Database,
    *,
    use_cooldown: bool = True,
) -> None:
    """Envia /like exclusivamente pela Auto System."""
    status_msg = await message.reply("⏳ Enviando likes...")
    started_at = time.perf_counter()

    if not autosystem_likes_api.enabled:
        if use_cooldown:
            await db.release_like_slot(message.from_user.id)
        await status_msg.edit_text(
            "⚠️ <b>Serviço de likes temporariamente indisponível.</b>\n"
            "Avise um administrador."
        )
        return

    result = await autosystem_likes_api.send_like(game_id)
    data = result.data if isinstance(result.data, dict) else {}
    error = (result.error or "").strip()

    if result.ok:
        nick = data.get("nickname") or "Jogador"
        before = data.get("likes_before")
        after = data.get("likes_after")
        region = data.get("region") or "BR"

        before_int = _to_counter_int(before)
        after_int = _to_counter_int(after)
        confirmed_int = _to_counter_int(data.get("likes_confirmed"))
        reported_int = _to_counter_int(data.get("likes_sent"))

        if before_int is not None and after_int is not None and after_int >= before_int:
            sent_int = after_int - before_int
        elif confirmed_int is not None:
            sent_int = max(0, confirmed_int)
        elif reported_int is not None:
            sent_int = max(0, reported_int)
        else:
            sent_int = 0

        if use_cooldown:
            if sent_int > 0:
                await db.complete_like_slot(message.from_user.id)
            else:
                await db.release_like_slot(message.from_user.id)

        elapsed = time.perf_counter() - started_at
        logger.info(
            "LIKE Auto System | uid=%s antes=%s depois=%s enviado=%s",
            game_id,
            before_int,
            after_int,
            sent_int,
        )

        await status_msg.edit_text(
            texts.LIKE_SUCCESS.format(
                game_id=game_id,
                nick=html.escape(str(nick)),
                region=html.escape(str(region)),
                antes=html.escape(str(before)) if before is not None else "—",
                depois=html.escape(str(after)) if after is not None else "—",
                enviadas=sent_int,
                tempo=f"{elapsed:.2f}",
            ),
            reply_markup=await group_store_markup(message),
        )
        # Aviso no grupo só para envios feitos no privado do bot.
        if sent_int > 0 and message.chat.type == "private":
            try:
                await announce_autolike_send(
                    message.bot,
                    db,
                    user_id=message.from_user.id,
                    plan_name="Envio de Likes",
                    uid=game_id,
                    nickname=str(nick),
                    likes_sent=sent_int,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao anunciar /like uid=%s", game_id)
        return

    if use_cooldown:
        await db.release_like_slot(message.from_user.id)

    logger.warning(
        "Falha Auto System no /like uid=%s status=%s error=%s data=%s",
        game_id,
        result.status,
        error,
        data,
    )

    if result.status == 400:
        await status_msg.edit_text(
            "❌ Não foi possível processar esse ID. Confira os números e tente novamente."
        )
        return

    if result.status in (401, 403):
        await status_msg.edit_text(
            "⚠️ O serviço de likes está temporariamente indisponível. "
            "Avise um administrador."
        )
        return

    if result.status == 404:
        # HTTP 404 isolado nao comprova que o jogador nao existe.
        # Exigir sinal explicito da API antes de exibir erro definitivo.
        code = str(data.get("code") or "").upper()
        detail = str(error or "").lower()
        confirmed_missing = code in {"PLAYER_NOTFOUND", "PLAYER_NOT_FOUND", "UID_NOT_FOUND"}
        if confirmed_missing and ("jogador" in detail or "player" in detail or "uid" in detail):
            await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        else:
            await status_msg.edit_text(
                "⚠️ Não foi possível confirmar o perfil desse ID agora. "
                "Tente novamente em alguns minutos. Nenhum envio foi confirmado."
            )
        return

    if result.status == 409:
        cooldown_text = data.get("tempo_restante") or ""
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(
                game_id=game_id,
                cooldown=(
                    f"⏱ {html.escape(str(cooldown_text))}"
                    if cooldown_text
                    else ""
                ),
            ),
            reply_markup=await group_store_markup(message),
        )
        return

    if result.status == 429:
        await status_msg.edit_text(
            "⏳ Limite temporário atingido. Tente novamente em instantes."
        )
        return

    if result.status == 503:
        await status_msg.edit_text(
            "⏳ Serviço temporariamente indisponível. Tente novamente em instantes."
        )
        return

    if error:
        await status_msg.edit_text("⚠️ Não foi possível concluir o envio agora. Tente novamente mais tarde.")
        return

    await status_msg.edit_text(texts.GENERIC_ERROR)


@router.message(Command("like", "likes"))
async def cmd_like(
    message: Message,
    command: CommandObject,
    autosystem_likes_api: AutoSystemLikesApi,
    db: Database,
    config: Config,
    bot: Bot,
) -> None:
    if not message.from_user:
        return

    await _delete_user_command(message, bot)
    owner_id = await resolve_owner_id(message, bot, config)
    is_owner = owner_id is not None
    is_vip = await db.is_vip(message.from_user.id)

    # Usuário comum/VIP segue usando /like em grupos.
    # Donos têm acesso total e podem usar também no privado.
    if message.chat.type not in GROUP_TYPES and not is_owner:
        await message.answer(texts.LIKE_PRIVATE_BLOCKED)
        return

    # Grupos administrados pelos donos são liberados. Outros grupos precisam
    # ter uma licença de aluguel ativa.
    if message.chat.type in GROUP_TYPES and not is_owner:
        owner_group = await group_has_owner_admin(message, bot, config)
        if not owner_group:
            rental = await db.active_rental_for_chat(message.chat.id)
            if not rental:
                await message.reply(
                    "🔒 <b>Bot sem licença ativa neste grupo.</b>\n\n"
                    "Para usar o envio de likes aqui, alugue o bot pelo menu "
                    "no privado e vincule a licença a este grupo.\n\n"
                    "🛒 Na loja também tem Auto Like e Passe de Elite.",
                    reply_markup=await group_store_markup(message),
                )
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

    use_cooldown = not (is_owner or is_vip)

    if use_cooldown:
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
                f"Você poderá usar novamente em <b>{wait_text}</b>.\n\n"
                "💎 Usuários VIP não possuem esse limite.",
                reply_markup=await group_store_markup(message),
            )
            return

    try:
        await _do_send_like(
            message,
            game_id,
            autosystem_likes_api,
            db,
            use_cooldown=use_cooldown,
        )
    except Exception:
        if use_cooldown:
            await db.release_like_slot(message.from_user.id)
        raise


@router.message(Command("like2"))
async def cmd_like2(
    message: Message,
    command: CommandObject,
    db: Database,
    config: Config,
    ffhub_shop: FFHubShopApi,
    bot: Bot,
) -> None:
    """Dono envia direto; clientes recebem os pacotes pagos."""
    if not message.from_user:
        return

    await _delete_user_command(message, bot)
    owner_id = await resolve_owner_id(message, bot, config)
    is_owner = owner_id is not None
    is_vip2 = await db.is_vip2(message.from_user.id)
    arg = (command.args or "").strip()

    has_direct_access = is_owner or is_vip2

    # Sem UID: dono/VIP2/grupo do dono recebe instrução do envio direto.
    if not arg and has_direct_access:
        perfil = (
            "DONO"
            if is_owner
            else "VIP2"
            if is_vip2
            else "GRUPO DO DONO"
        )
        await message.reply(
            f"🔥 <b>LIKE2 • {perfil}</b>\n\n"
            "Use assim:\n"
            "<code>/like2 SEU_ID</code>\n\n"
            "✅ Pode usar em grupos.\n"
            "✅ Envio direto de likes."
        )
        return

    # /like2 UID é uma porta separada: dono, VIP2 ou grupo validado do dono.
    # Quem não passar daqui NÃO chega à FFHub.
    if arg and not has_direct_access:
        await message.reply(
            "🔒 <b>LIKE2 exclusivo para VIP2</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Você não possui acesso VIP2 ativo.\n\n"
            "Nenhuma requisição foi enviada.\n"
            "Use <code>/vip2</code> para consultar seu acesso."
        )
        return

    # Daqui pra baixo só entra dono/VIP2/grupo do dono com UID informado.
    if arg and has_direct_access:
        game_id = clean_game_id(arg.split()[0])
        if not _valid_like_uid(game_id):
            await message.reply(
                "⚠️ ID inválido. Envie somente números, de 8 a 11 dígitos.\n"
                "Exemplo: <code>/like2 813856263</code>"
            )
            return

        if not ffhub_shop.configured:
            await message.reply(
                "⚠️ <b>LIKE2 temporariamente indisponível.</b>\n\n"
                "O serviço de envio ainda não está disponível."
            )
            return

        # VIP2 pode fazer somente 1 envio concluído a cada 24 horas.
        # Donos continuam sem limite.
        vip2_slot_reserved = False
        if is_vip2 and not is_owner:
            allowed, remaining = await db.acquire_vip2_slot(message.from_user.id)
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
                    "⏳ <b>Limite diário do VIP2 atingido.</b>\n\n"
                    "Seu VIP2 permite <b>1 envio de Like2 a cada 24 horas</b>.\n"
                    f"Você poderá enviar novamente em <b>{wait_text}</b>."
                )
                return
            vip2_slot_reserved = True

        status_msg = await message.reply("🔥 Enviando Like2...")
        started_at = time.perf_counter()
        access_type = (
            "owner"
            if is_owner
            else "vip2"
            if is_vip2
            else "owner_group"
        )
        logger.info(
            "LIKE2 inicio | acesso=%s user=%s uid=%s",
            access_type,
            message.from_user.id,
            game_id,
        )
        try:
            result = await asyncio.wait_for(
                ffhub_shop.send_paid_likes(game_id),
                timeout=60,
            )
        except asyncio.TimeoutError:
            if vip2_slot_reserved:
                await db.release_vip2_slot(message.from_user.id)
            elapsed = time.perf_counter() - started_at
            logger.error(
                "LIKE2 timeout externo | acesso=%s user=%s uid=%s elapsed=%.2fs",
                access_type,
                message.from_user.id,
                game_id,
                elapsed,
            )
            await status_msg.edit_text(
                "⏱️ <b>LIKE2 sem resposta do serviço</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                "O serviço não respondeu em até <b>60 segundos</b>.\n\n"
                "⚠️ Não vou repetir automaticamente para evitar envio duplicado."
            )
            return
        except Exception as exc:  # noqa: BLE001
            if vip2_slot_reserved:
                await db.release_vip2_slot(message.from_user.id)
            logger.exception("Erro inesperado no /like2 uid=%s", game_id)
            await status_msg.edit_text(
                "❌ <b>Erro no LIKE2</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                "⚠️ Não foi possível concluir o envio. Tente novamente mais tarde."
            )
            return

        logger.info(
            "LIKE2 fim | acesso=%s user=%s uid=%s status=%s ok=%s elapsed=%.2fs",
            access_type,
            message.from_user.id,
            game_id,
            result.status,
            result.ok,
            time.perf_counter() - started_at,
        )

        data = result.data if isinstance(result.data, dict) else {}

        if not result.ok:
            if vip2_slot_reserved:
                await db.release_vip2_slot(message.from_user.id)
            error = (
                result.error
                or data.get("erro")
                or data.get("error")
                or data.get("mensagem")
                or data.get("message")
                or "Não foi possível concluir o envio."
            )
            await status_msg.edit_text(
                "❌ <b>LIKE2 não enviado</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                "⚠️ Não foi possível concluir o envio agora. Tente novamente mais tarde."
            )
            return

        if vip2_slot_reserved:
            await db.complete_vip2_slot(message.from_user.id)

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
                "✅ Envio concluído com sucesso.",
            ]
        )
        await status_msg.edit_text(
            "\n".join(lines),
            reply_markup=await group_store_markup(message),
        )
        try:
            sent_value = int(sent) if sent is not None else 0
            # Aviso no grupo só para envios feitos no privado do bot.
            if sent_value > 0 and message.chat.type == "private":
                await announce_autolike_send(
                    message.bot,
                    db,
                    user_id=message.from_user.id,
                    plan_name="LIKE2",
                    uid=game_id,
                    nickname=str(nick or "Jogador"),
                    likes_sent=sent_value,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao anunciar /like2 uid=%s", game_id)
        return

    # Clientes não usam mais o menu antigo do /like2.
    # O Auto Like 500 está disponível somente no menu principal novo.
    if message.chat.type == "private":
        await message.answer(
            "💎 <b>Auto Like 500</b>\n\n"
            "A loja antiga do /like2 foi removida.\n"
            "Use o menu principal abaixo:",
            reply_markup=main_menu(False),
        )
    else:
        await message.reply(
            "ℹ️ O Auto Like 500 agora é comprado pelo menu privado do bot.\n"
            "Abra o privado e use <code>/start</code>."
        )
