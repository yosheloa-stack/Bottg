"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import texts
from app.catalog import resolve_products
from app.config import Config
from app.database import Database
from app.keyboards.inline import premium_like_menu
from app.services.autolike import AutoLikeApi
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


def _to_counter_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip().replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _extract_profile_likes(data: dict) -> int | None:
    """Extrai curtidas de respostas diferentes da API de info."""
    if not isinstance(data, dict):
        return None

    candidates = [data]
    for key in ("player", "jogador", "basicInfo", "basic_info", "data"):
        value = data.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    for src in candidates:
        for key in (
            "likes",
            "curtidas",
            "accountLikes",
            "account_likes",
            "like",
            "liked",
        ):
            value = _to_counter_int(src.get(key))
            if value is not None:
                return value
    return None


async def _profile_likes(autolike: AutoLikeApi, game_id: str) -> int | None:
    try:
        result = await autolike.info_player(game_id)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao consultar likes do perfil uid=%s", game_id)
        return None
    if not result.ok or not isinstance(result.data, dict):
        return None
    return _extract_profile_likes(result.data)


async def _do_send_like(
    message: Message,
    game_id: str,
    likes_api: LikesApi,
    autosystem_likes_api: AutoSystemLikesApi,
    autolike: AutoLikeApi,
    db: Database,
    *,
    use_cooldown: bool = True,
) -> None:
    status_msg = await message.reply("⏳ Enviando likes...")
    started_at = time.perf_counter()

    # Guarda um contador independente antes do envio. Ele serve para confirmar
    # entregas mesmo quando a Likes Painel processa mas não devolve a resposta.
    verified_before = await _profile_likes(autolike, game_id)
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
            "LIKE real | uid=%s antes=%s depois=%s enviado=%s",
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
            )
        )
        return

    # Timeout após o envio: verifica o contador real por outra rota antes
    # de assumir falha. Isso evita falso erro e também evita envio duplicado.
    if data.get("delivery_unknown"):
        verified_after = None
        for delay in (2, 4, 6):
            await asyncio.sleep(delay)
            verified_after = await _profile_likes(autolike, game_id)
            if (
                verified_before is not None
                and verified_after is not None
                and verified_after > verified_before
            ):
                break

        if (
            verified_before is not None
            and verified_after is not None
            and verified_after > verified_before
        ):
            sent_int = verified_after - verified_before
            elapsed = time.perf_counter() - started_at

            if use_cooldown:
                await db.complete_like_slot(message.from_user.id)

            logger.info(
                "LIKE confirmado apos timeout | uid=%s antes=%s depois=%s real=%s",
                game_id,
                verified_before,
                verified_after,
                sent_int,
            )

            await status_msg.edit_text(
                texts.LIKE_SUCCESS.format(
                    game_id=game_id,
                    nick="Jogador",
                    region="BR",
                    antes=verified_before,
                    depois=verified_after,
                    enviadas=sent_int,
                    tempo=f"{elapsed:.2f}",
                )
            )
            return

        logger.warning(
            "LIKE ainda incerto apos verificacao | uid=%s antes=%s depois=%s",
            game_id,
            verified_before,
            verified_after,
        )
        await status_msg.edit_text(
            "⏳ <b>Envio processado, confirmação pendente</b>\n\n"
            f"🆔 UID: <code>{game_id}</code>\n"
            "Não consegui confirmar uma mudança no contador ainda.\n\n"
            "⚠️ Não vou repetir automaticamente para evitar envio duplicado."
        )
        return

    if use_cooldown:
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
    autolike: AutoLikeApi,
    db: Database,
    config: Config,
) -> None:
    if not message.from_user:
        return

    is_owner = message.from_user.id in config.admin_ids
    is_vip = await db.is_vip(message.from_user.id)

    # Usuário comum/VIP segue usando /like em grupos.
    # Donos têm acesso total e podem usar também no privado.
    if message.chat.type not in GROUP_TYPES and not is_owner:
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
                "💎 Usuários VIP não possuem esse limite."
            )
            return

    try:
        await _do_send_like(
            message,
            game_id,
            likes_api,
            autosystem_likes_api,
            autolike,
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

    is_owner = message.from_user.id in config.admin_ids
    is_vip2 = await db.is_vip2(message.from_user.id)
    arg = (command.args or "").strip()

    # Alguns grupos fazem o Telegram entregar a mensagem como o próprio chat
    # (sender_chat == chat), mesmo quando o usuário diz não ter ativado
    # "administrador anônimo". Nesse caso o from_user deixa de ser o ID real.
    sent_as_group = (
        message.chat.type in GROUP_TYPES
        and message.sender_chat is not None
        and message.sender_chat.id == message.chat.id
    )

    owner_group_access = False
    if sent_as_group:
        try:
            admins = await bot.get_chat_administrators(message.chat.id)
            owner_admins = [
                member
                for member in admins
                if getattr(member, "user", None)
                and member.user.id in config.admin_ids
            ]
            # Só reconhece o envio "como grupo" quando existe um dos donos
            # configurados entre os administradores reais deste chat.
            owner_group_access = bool(owner_admins)
            logger.info(
                "LIKE2 sender_chat | chat=%s owners_admin=%s autorizado=%s",
                message.chat.id,
                [m.user.id for m in owner_admins],
                owner_group_access,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Falha ao validar administradores do grupo no /like2 chat=%s",
                message.chat.id,
            )

    has_direct_access = is_owner or is_vip2 or owner_group_access

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
            "✅ Envio direto pela FFHub."
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
                "A integração FFHub ainda não está configurada."
            )
            return

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
            elapsed = time.perf_counter() - started_at
            logger.error(
                "LIKE2 timeout externo | acesso=%s user=%s uid=%s elapsed=%.2fs",
                access_type,
                message.from_user.id,
                game_id,
                elapsed,
            )
            await status_msg.edit_text(
                "⏱️ <b>LIKE2 sem resposta da FFHub</b>\n\n"
                f"🆔 UID: <code>{game_id}</code>\n"
                "A API não respondeu em até <b>60 segundos</b>.\n\n"
                "⚠️ Não vou repetir automaticamente para evitar envio duplicado."
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
                f"✅ Envio concluído via <b>{'DONO' if (is_owner or owner_group_access) else 'VIP2'}</b>.",
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

