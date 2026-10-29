"""Painel administrativo: produtos (preço/estoque), estatísticas, estoque API, broadcast.

Todas as ações são restritas aos IDs em ADMIN_IDS.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.catalog import resolve_product, resolve_products
from app.config import Config
from app.database import Database
from app.owner_auth import resolve_owner_id
from app.keyboards.inline import back_home
from app.services.autolike import AutoLikeApi
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.ffhub_shop import FFHubShopApi
from app.services.likes import LikesApi
from app.services.passe import PasseApi
from app.services.payments import MercadoPagoGateway
from app.services.support_ai import SquareCloudAI
from app.states import AdminBroadcast, AdminPrice, AdminStock, OwnerSetting
from app.ui import edit_screen
from app.utils import format_price

logger = logging.getLogger(__name__)

router = Router(name="admin")
OWNER_IDS = {8204579375, 8669377135}

OWNER_SETTING_LABELS = {
    "FFHUB_API_KEY": "FFHub API Key",
    "LIKES_API_KEY": "Likes API Key",
    "AUTOSYSTEM_API_KEY": "AutoSystem API Key",
    "PASSE_API_KEY": "Passe API Key",
    "MERCADOPAGO_ACCESS_TOKEN": "Mercado Pago Access Token",
    "SQUARECLOUD_AI_KEY": "Square Cloud AI Key (suporte)",
}


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


async def _deny(query: CallbackQuery) -> None:
    await query.answer("Sem permissão.", show_alert=True)


PRODUCT_ALIASES = {
    "220": "autolike220",
    "auto220": "autolike220",
    "autolike220": "autolike220",
    "500": "autolike500",
    "auto500": "autolike500",
    "autolike500": "autolike500",
    "revenda": "reseller_access",
    "reseller": "reseller_access",
    "aluguel": "bot_rental",
    "bot": "bot_rental",
    "passe": "passe",
    # Legado
    "7": "like2_7d",
    "7d": "like2_7d",
    "15": "like2_15d",
    "15d": "like2_15d",
    "30": "like2_30d",
    "30d": "like2_30d",
    "unico": "like2_single",
    "único": "like2_single",
    "single": "like2_single",
    "like2": "like2_single",
}


def _parse_price(raw: str) -> Decimal | None:
    normalized = (
        (raw or "")
        .strip()
        .upper()
        .replace("R$", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        value = Decimal(normalized)
        if value <= 0:
            return None
        return value.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _parse_stock(raw: str) -> int | None:
    try:
        value = int((raw or "").strip().replace(" ", ""))
    except ValueError:
        return None
    if value < -1:
        return None
    return value


def _resolve_product_code(raw: str) -> str:
    key = (raw or "").strip().lower()
    return PRODUCT_ALIASES.get(key, key)


async def _save_price(
    message: Message,
    db: Database,
    config: Config,
    code: str,
    value: Decimal,
) -> None:
    await db.set_price(code, f"{value:.2f}")
    rp = await resolve_product(config, db, code)
    logger.info(
        "ADMIN preço salvo | user=%s produto=%s valor=%s",
        message.from_user.id if message.from_user else None,
        code,
        value,
    )
    if not rp:
        await message.answer("⚠️ Preço salvo, mas o produto não foi localizado.")
        return
    service_line = (
        f"\n⚠️ Serviço: <b>{rp.service_issue or 'integração não configurada'}</b>"
        if not rp.service_ready
        else ""
    )
    await message.answer(
        "✅ <b>PREÇO SALVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produto: <b>{rp.title}</b>\n"
        f"💰 Novo preço: <b>R$ {format_price(rp.price)}</b>\n"
        f"📦 Estoque real: <b>{rp.stock_label}</b>"
        f"{service_line}\n\n"
        "A alteração já está salva no banco desta aplicação."
    )


async def _save_stock(
    message: Message,
    db: Database,
    config: Config,
    code: str,
    value: int,
) -> None:
    await db.set_stock(code, value)
    rp = await resolve_product(config, db, code)
    logger.info(
        "ADMIN estoque salvo | user=%s produto=%s estoque=%s",
        message.from_user.id if message.from_user else None,
        code,
        value,
    )
    if not rp:
        await message.answer("⚠️ Estoque salvo, mas o produto não foi localizado.")
        return
    service_line = (
        f"\n⚠️ Serviço: <b>{rp.service_issue or 'integração não configurada'}</b>"
        if not rp.service_ready
        else ""
    )
    await message.answer(
        "✅ <b>ESTOQUE SALVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Produto: <b>{rp.title}</b>\n"
        f"🔢 Novo estoque: <b>{rp.stock_label}</b>\n"
        f"💰 Preço atual: <b>R$ {format_price(rp.price)}</b>"
        f"{service_line}\n\n"
        "A alteração já está salva no banco desta aplicação."
    )


# ---------- Entrada ----------
@router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config, bot: Bot) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return
    await message.answer(
        "⚙️ <b>COMANDOS DO DONO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<code>/produtos</code> — diagnóstico completo\n"
        "<code>/preco 220 0,60</code> — preço por dia Auto Like 220\n"
        "<code>/preco 500 2,00</code> — preço por dia Auto Like 500\n"
        "<code>/preco passe 4,00</code>\n"
        "<code>/preco revenda 20,00</code>\n"
        "<code>/preco aluguel 15,00</code>\n"
        "<code>/estoque 220 -1</code> — -1 = ilimitado\n"
        "<code>/descontorevenda 20</code> — desconto em %\n"
        "<code>/setkey ffhub CHAVE</code>\n"
        "<code>/setkey autosystem CHAVE</code>\n"
        "<code>/setkey mp ACCESS_TOKEN</code>\n"
        "<code>/setkey passe CHAVE</code>\n"
        "<code>/setkey ia CHAVE</code> — IA do suporte (Square Cloud)\n"
        "<code>/binance</code> — pagamento USDT confirmado por e-mail\n"
        "<code>/giftcard 20</code> — gera Gift Card de R$ 20\n"
        "<code>/giftcard 20 5</code> — gera com 5 usos\n"
        "<code>/anuncios</code> — usar dentro do grupo de avisos\n"
        "<code>/anuncios off</code> — desliga os avisos\n"
        "<code>/setmenu</code> — responder uma foto/GIF/vídeo\n"
        "<code>/setmenu reset</code> — volta ao banner padrão\n"
        "<code>/backup</code> — backup do banco agora\n"
        "<code>/restaurar</code> — responder um arquivo .db de backup\n\n"
        "VIP: <code>/vip</code> · VIP2: <code>/vip2</code>\n"
        "Auto Like manual: <code>/autolike</code>"
    )


# ---------- Atalhos diretos de dono (sem FSM) ----------
@router.message(Command("preco"))
async def cmd_set_price_direct(
    message: Message,
    config: Config,
    db: Database,
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.reply(
            "Use: <code>/preco 220 0,60</code>\n"
            "Produtos: <code>220</code>, <code>500</code>, <code>passe</code>, "
            "<code>revenda</code> ou <code>aluguel</code>."
        )
        return
    code = _resolve_product_code(parts[1])
    if code not in config.products:
        await message.reply("⚠️ Produto inválido.")
        return
    value = _parse_price(parts[2])
    if value is None:
        await message.reply("⚠️ Preço inválido.")
        return
    await _save_price(message, db, config, code, value)


@router.message(Command("estoque"))
async def cmd_set_stock_direct(
    message: Message,
    config: Config,
    db: Database,
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.reply(
            "Use: <code>/estoque 220 -1</code>\n"
            "Use <code>-1</code> para ilimitado."
        )
        return
    code = _resolve_product_code(parts[1])
    if code not in config.products:
        await message.reply("⚠️ Produto inválido.")
        return
    value = _parse_stock(parts[2])
    if value is None:
        await message.reply("⚠️ Estoque inválido.")
        return
    await _save_stock(message, db, config, code, value)


@router.message(Command("produtos"))
async def cmd_products_status(
    message: Message,
    config: Config,
    db: Database,
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return
    products = await resolve_products(config, db)
    saved = await db.all_owner_settings()
    ffhub_ok = bool((saved.get("FFHUB_API_KEY") or config.ffhub_api_key or "").strip())
    mp_ok = bool(
        (saved.get("MERCADOPAGO_ACCESS_TOKEN") or config.mp_access_token or "").strip()
    )
    lines = [
        "🧪 <b>DIAGNÓSTICO DOS PRODUTOS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🌐 Instância: <code>{config.webhook_public_url or 'sem URL pública'}</code>",
        f"💎 FFHub: <b>{'✅ Configurada' if ffhub_ok else '❌ Não configurada'}</b>",
        f"💳 Mercado Pago: <b>{'✅ Configurado' if mp_ok else '❌ Não configurado'}</b>",
        "",
    ]
    discount = await db.get_owner_setting("RESELLER_DISCOUNT_PERCENT") or "20"
    lines.append(f"🤝 Desconto revenda: <b>{discount}%</b>")
    lines.append("")
    for code in (
        "autolike220",
        "autolike500",
        "passe",
        "reseller_access",
        "bot_rental",
        "like2_single",
    ):
        rp = products.get(code)
        if not rp:
            continue
        lines.extend(
            [
                f"📦 <b>{rp.title}</b>",
                f"• Código: <code>{code}</code>",
                f"• Preço: <b>R$ {format_price(rp.price)}</b>",
                f"• Estoque real: <b>{rp.stock_label}</b>",
                f"• Serviço: <b>{'OK' if rp.service_ready else rp.service_issue}</b>",
                f"• Venda: <b>{'LIBERADA' if rp.available else 'BLOQUEADA'}</b>",
                "",
            ]
        )
    await message.reply("\n".join(lines))


@router.message(Command("descontorevenda"))
async def cmd_reseller_discount(
    message: Message,
    config: Config,
    db: Database,
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply(
            "Use: <code>/descontorevenda 20</code>\n"
            "Aceito de 0 a 80%."
        )
        return
    try:
        value = Decimal(parts[1].replace(",", "."))
    except (InvalidOperation, ValueError):
        await message.reply("⚠️ Percentual inválido.")
        return
    if value < 0 or value > 80:
        await message.reply("⚠️ Use um desconto entre 0 e 80%.")
        return

    value = value.quantize(Decimal("0.01"))
    await db.set_owner_setting("RESELLER_DISCOUNT_PERCENT", str(value))
    await message.reply(
        "✅ <b>DESCONTO DE REVENDA ATUALIZADO</b>\n"
        f"Novo desconto automático: <b>{value}%</b>"
    )


@router.message(Command("setkey"))
async def cmd_setkey(
    message: Message,
    config: Config,
    db: Database,
    likes_api: LikesApi,
    autosystem_likes_api: AutoSystemLikesApi,
    ffhub_shop: FFHubShopApi,
    passe: PasseApi,
    gateway: MercadoPagoGateway,
    support_ai: SquareCloudAI,
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return
    if message.chat.type != "private":
        await message.reply(
            "🔐 Por segurança, envie <code>/setkey</code> somente no privado do bot."
        )
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.reply(
            "Use no privado:\n"
            "<code>/setkey ffhub SUA_CHAVE</code>\n"
            "<code>/setkey autosystem SUA_CHAVE</code>\n"
            "<code>/setkey mp ACCESS_TOKEN</code>\n"
            "<code>/setkey passe SUA_CHAVE</code>\n"
            "<code>/setkey ia SUA_CHAVE</code>"
        )
        return

    alias = parts[1].strip().lower()
    value = parts[2].strip()
    mapping = {
        "ffhub": "FFHUB_API_KEY",
        "autosystem": "AUTOSYSTEM_API_KEY",
        "mp": "MERCADOPAGO_ACCESS_TOKEN",
        "mercadopago": "MERCADOPAGO_ACCESS_TOKEN",
        "passe": "PASSE_API_KEY",
        "likes": "LIKES_API_KEY",
        "ia": "SQUARECLOUD_AI_KEY",
        "ai": "SQUARECLOUD_AI_KEY",
    }
    key = mapping.get(alias)
    if not key or not value:
        await message.reply("⚠️ Tipo de chave inválido.")
        return

    await db.set_owner_setting(key, value)
    if key == "FFHUB_API_KEY":
        ffhub_shop._key = value
    elif key == "AUTOSYSTEM_API_KEY":
        autosystem_likes_api._key = value
    elif key == "MERCADOPAGO_ACCESS_TOKEN":
        gateway._token = value
    elif key == "PASSE_API_KEY":
        passe._key = value
    elif key == "LIKES_API_KEY":
        likes_api._key = value
    elif key == "SQUARECLOUD_AI_KEY":
        support_ai._key = value

    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.send_message(
        message.chat.id,
        "✅ <b>Chave atualizada e aplicada.</b>\n"
        "O valor enviado foi removido da conversa quando possível."
    )

    if key == "SQUARECLOUD_AI_KEY":
        # Testa a chave na hora para o dono saber se a IA vai funcionar.
        masked = f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "curta demais"
        result = await support_ai.chat(
            [{"role": "user", "content": "Responda apenas: ok"}], max_tokens=10
        )
        if result.ok:
            text = f"🤖 IA testada e funcionando (chave {masked})."
        else:
            text = (
                f"⚠️ A Square Cloud recusou a chave {masked}: <code>{result.error}</code>\n"
                "Use uma API key com o escopo <code>ai:chat</code> e sem nenhuma "
                "aplicação marcada em Recursos."
            )
        await message.bot.send_message(message.chat.id, text)


@router.message(Command("giftcard"))
async def cmd_giftcard(
    message: Message,
    config: Config,
    db: Database,
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split()
    if len(parts) >= 2 and parts[1].lower() in {"revoke", "revogar", "cancelar"}:
        if len(parts) < 3:
            await message.reply("Use: <code>/giftcard revoke CODIGO</code>")
            return
        ok = await db.revoke_gift_card(parts[2])
        await message.reply(
            "✅ Gift Card revogado."
            if ok
            else "⚠️ Gift Card não encontrado."
        )
        return

    if len(parts) >= 2 and parts[1].lower() in {"status", "ver"}:
        if len(parts) < 3:
            await message.reply("Use: <code>/giftcard status CODIGO</code>")
            return
        card = await db.get_gift_card(parts[2])
        if not card:
            await message.reply("⚠️ Gift Card não encontrado.")
            return
        await message.reply(
            "🎁 <b>GIFT CARD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Código: <code>{card['code']}</code>\n"
            f"💰 Valor: <b>R$ {format_price(int(card['amount_cents']) / 100)}</b>\n"
            f"♻️ Usos: <b>{card['uses_count']}/{card['max_uses']}</b>\n"
            f"📌 Status: <b>{'Ativo' if int(card['is_active']) else 'Revogado'}</b>"
        )
        return

    if len(parts) < 2:
        await message.reply(
            "Use:\n"
            "<code>/giftcard 20</code> — R$ 20, 1 uso\n"
            "<code>/giftcard 20 5</code> — R$ 20, 5 usos"
        )
        return

    value = _parse_price(parts[1])
    if value is None:
        await message.reply("⚠️ Valor inválido.")
        return

    max_uses = 1
    if len(parts) >= 3:
        try:
            max_uses = int(parts[2])
        except ValueError:
            await message.reply("⚠️ Quantidade de usos inválida.")
            return
        if not 1 <= max_uses <= 1000:
            await message.reply("⚠️ Use entre 1 e 1000 usos.")
            return

    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = (
            "AURORA-"
            + "".join(secrets.choice(alphabet) for _ in range(4))
            + "-"
            + "".join(secrets.choice(alphabet) for _ in range(4))
        )
        if not await db.get_gift_card(code):
            break
    else:
        await message.reply("⚠️ Não consegui gerar um código único agora.")
        return

    amount_cents = int((value * 100).to_integral_value())
    await db.create_gift_card(
        code,
        amount_cents,
        message.from_user.id,
        max_uses=max_uses,
    )
    await message.reply(
        "🎁 <b>GIFT CARD GERADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <code>{code}</code>\n"
        f"💰 Valor: <b>R$ {format_price(value)}</b>\n"
        f"♻️ Usos: <b>{max_uses}</b>\n\n"
        "O cliente pode usar:\n"
        f"<code>/resgatar {code}</code>"
    )


@router.message(Command("anuncios"))
async def cmd_announcements_group(
    message: Message,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    action = parts[1].strip().lower() if len(parts) > 1 else ""

    if action in {"off", "desligar", "disable"}:
        await db.set_owner_setting("ANNOUNCEMENT_CHAT_ID", "")
        await db.set_owner_setting("ANNOUNCEMENT_THREAD_ID", "")
        await message.reply("✅ Avisos públicos desativados.")
        return

    if action in {"status", "ver"}:
        chat_id = await db.get_owner_setting("ANNOUNCEMENT_CHAT_ID")
        thread_id = await db.get_owner_setting("ANNOUNCEMENT_THREAD_ID")
        await message.reply(
            "📣 <b>GRUPO DE ANÚNCIOS</b>\n"
            f"Chat: <code>{chat_id or 'não configurado'}</code>\n"
            f"Tópico: <code>{thread_id or '—'}</code>"
        )
        return

    if message.chat.type not in {"group", "supergroup"}:
        await message.reply(
            "📣 Envie <code>/anuncios</code> dentro do grupo onde quer receber "
            "os avisos de compras e envios."
        )
        return

    thread_id = message.message_thread_id
    await db.set_owner_setting("ANNOUNCEMENT_CHAT_ID", str(message.chat.id))
    await db.set_owner_setting(
        "ANNOUNCEMENT_THREAD_ID",
        str(thread_id) if thread_id is not None else "",
    )
    await message.reply(
        "✅ <b>GRUPO DE ANÚNCIOS CONFIGURADO</b>\n\n"
        "Compras confirmadas e envios de Auto Like serão publicados aqui "
        "com o botão <b>Adquirir</b>."
    )


@router.message(Command("setmenu"))
async def cmd_set_menu_media(
    message: Message,
    config: Config,
    db: Database,
    bot: Bot,
) -> None:
    owner_id = await resolve_owner_id(message, bot, config)
    if owner_id is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    action = parts[1].strip().lower() if len(parts) > 1 else ""
    if action in {"reset", "padrao", "padrão"}:
        await db.set_owner_setting("MENU_MEDIA", "")
        await message.reply("✅ Mídia do menu voltou ao padrão.")
        return

    replied = message.reply_to_message
    if not replied:
        await message.reply(
            "Responda uma <b>foto, GIF ou vídeo</b> com <code>/setmenu</code>.\n"
            "Para restaurar: <code>/setmenu reset</code>"
        )
        return

    media = ""
    label = ""
    if replied.animation:
        media = f"tg:animation:{replied.animation.file_id}"
        label = "GIF"
    elif replied.video:
        media = f"tg:video:{replied.video.file_id}"
        label = "vídeo"
    elif replied.photo:
        media = f"tg:photo:{replied.photo[-1].file_id}"
        label = "foto"

    if not media:
        await message.reply("⚠️ Responda uma foto, GIF ou vídeo válido.")
        return

    await db.set_owner_setting("MENU_MEDIA", media)
    await message.reply(
        f"✅ <b>{label.capitalize()} do menu atualizado.</b>\n"
        "O próximo <code>/start</code> já usa essa mídia."
    )


# ---------- Estatísticas ----------
@router.callback_query(F.data == "admin:stats")
async def cb_stats(query: CallbackQuery, config: Config, db: Database) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    users = await db.count_users()
    vips = await db.count_active_vips()
    vip2s = await db.count_active_vip2s()
    total = await db.count_orders()
    delivered = await db.count_orders("delivered")
    pending = await db.count_orders("pending")
    failed = await db.count_orders("failed")
    text = (
        "📊 <b>Estatísticas</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Usuários: <b>{users}</b>\n"
        f"💎 VIPs ativos: <b>{vips}</b>\n"
        f"🔥 VIP2 ativos: <b>{vip2s}</b>\n"
        f"🧾 Pedidos totais: <b>{total}</b>\n"
        f"✅ Entregues: <b>{delivered}</b>\n"
        f"⏳ Pendentes: <b>{pending}</b>\n"
        f"⚠️ Falhas: <b>{failed}</b>"
    )
    await edit_screen(query, text, admin_panel())
    await query.answer()


# ---------- Estoque da API de Passe ----------
@router.callback_query(F.data == "admin:estoque")
async def cb_estoque(query: CallbackQuery, config: Config, passe: PasseApi) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    await query.answer("Consultando estoque...")
    result = await passe.estoque()
    if not result.ok:
        text = "📦 <b>Estoque Passe (API)</b>\n\n⚠️ Não foi possível consultar."
    else:
        d = result.data if isinstance(result.data, dict) else {}
        passes = d.get("passes") or d.get("disponivel") or d.get("estoque") or "?"
        diamonds = d.get("diamonds") or d.get("diamantes") or "?"
        accounts = d.get("contas") or d.get("accounts") or "?"
        text = (
            "📦 <b>Estoque Passe (API)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🎟️ Passes disponíveis: <b>{passes}</b>\n"
            f"💎 Diamantes: <b>{diamonds}</b>\n"
            f"👤 Contas: <b>{accounts}</b>"
        )
    await edit_screen(query, text, admin_panel())


# ---------- Broadcast ----------
@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_start(
    query: CallbackQuery, state: FSMContext, config: Config
) -> None:
    if not _is_admin(query.from_user.id, config):
        return await _deny(query)
    await state.set_state(AdminBroadcast.waiting_message)
    await edit_screen(
        query,
        "📢 <b>Broadcast</b>\n\n"
        "Envie a mensagem que será disparada para todos os usuários.\n"
        "Use /cancel para abortar.",
        back_home(),
    )
    await query.answer()


@router.message(AdminBroadcast.waiting_message, F.text.regexp(r"^(?!/).+"))
async def broadcast_send(
    message: Message, state: FSMContext, config: Config, db: Database, bot: Bot
) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await state.clear()
    user_ids = await db.all_user_ids()
    await message.answer(f"📤 Enviando para {len(user_ids)} usuários...")

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await message.copy_to(uid)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"✅ Broadcast concluído.\nEnviados: <b>{sent}</b> · Falhas: <b>{failed}</b>",
        reply_markup=admin_panel(),
    )
