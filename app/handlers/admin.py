"""Painel administrativo: produtos (preço/estoque), estatísticas, estoque API, broadcast.

Todas as ações são restritas aos IDs em ADMIN_IDS.
"""
from __future__ import annotations

import asyncio
import logging
import re
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
        "<code>/setkey passe CHAVE</code>\n\n"
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
            "<code>/setkey passe SUA_CHAVE</code>"
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

    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.send_message(
        message.chat.id,
        "✅ <b>Chave atualizada e aplicada.</b>\n"
        "O valor enviado foi removido da conversa quando possível."
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
