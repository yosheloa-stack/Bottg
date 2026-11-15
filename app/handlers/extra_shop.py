"""Loja de produtos extras: presentes, diamantes, likes (envio único) e Snickers.

Também contém os comandos do dono para esses produtos (margem, preço de
presente, saldo da conta de produtos, estorno/conclusão manual).

Nenhum texto deste módulo para clientes cita provedor, dono ou API.
"""
from __future__ import annotations

import html
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.api_orders import ApiOrders, diamond_status_button, order_meta
from app.catalog import (
    gift_markup_percent,
    gift_price_for_user,
    price_for_user,
    resolve_product,
    resolve_products,
)
from app.config import DIAMOND_PACKS, Config, diamond_code
from app.database import Database
from app.keyboards.inline import back_home, checkout_menu
from app.services.ffhub_shop import FFHubShopApi, GiftItem, admin_error
from app.ui import edit_screen
from app.utils import clean_game_id, format_price, is_valid_game_id

logger = logging.getLogger(__name__)

router = Router(name="extra_shop")

UNAVAILABLE = (
    "⚠️ <b>Produto temporariamente indisponível.</b>\n\n"
    "Tente novamente mais tarde."
)
PAGE_SIZE = 8


class GiftFlow(StatesGroup):
    waiting_uid = State()


async def _ack(query: CallbackQuery, text: str | None = None) -> None:
    try:
        await query.answer(text)
    except Exception:  # noqa: BLE001
        pass


async def _show(
    target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup
) -> None:
    if isinstance(target, CallbackQuery):
        await edit_screen(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


def _user_id(target: Message | CallbackQuery) -> int:
    return target.from_user.id


def _back_more() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Voltar", callback_data="xs:menu")]


# ---------------------------------------------------------------------------
# Menu principal dos produtos extras
# ---------------------------------------------------------------------------
def more_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎁 Presentes", callback_data="xs:gifts"),
        InlineKeyboardButton(text="💎 Diamantes", callback_data="xs:dima"),
    )
    kb.row(
        InlineKeyboardButton(text="❤️ Likes (envio único)", callback_data="xs:likes"),
        InlineKeyboardButton(text="🍫 Snickers", callback_data="xs:snickers"),
    )
    kb.row(InlineKeyboardButton(text="⬅️ Voltar", callback_data="menu:products"))
    return kb.as_markup()


MORE_TEXT = (
    "🛍️ <b>MAIS PRODUTOS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎁 <b>Presentes</b> — itens enviados direto pelo UID\n"
    "💎 <b>Diamantes</b> — recarga pelo token Rewards\n"
    "❤️ <b>Likes</b> — um envio de likes na hora\n"
    "🍫 <b>Snickers</b> — resgate pelo token Rewards\n\n"
    "Pagamento por PIX ou saldo. Escolha uma opção:"
)


@router.callback_query(F.data.in_({"shop:more", "xs:menu"}))
async def cb_more(query: CallbackQuery, state: FSMContext) -> None:
    await _ack(query)
    await state.clear()
    await edit_screen(query, MORE_TEXT, more_menu())


@router.message(Command("produtos2", "maisprodutos"), F.chat.type == "private")
async def cmd_more(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MORE_TEXT, reply_markup=more_menu())


# ---------------------------------------------------------------------------
# Presentes
# ---------------------------------------------------------------------------
async def _available_gifts(api: FFHubShopApi) -> list[GiftItem]:
    return [g for g in await api.gift_catalog() if g.available]


def _categories(items: list[GiftItem]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item.category not in seen:
            seen.append(item.category)
    return seen


async def _gift_categories_screen(
    target: Message | CallbackQuery,
    config: Config,
    db: Database,
    api: FFHubShopApi,
) -> None:
    rp = await resolve_product(config, db, "gift")
    if not rp or not rp.service_ready or rp.stock == 0:
        await _show(target, UNAVAILABLE, more_menu())
        return
    items = await _available_gifts(api)
    if not items:
        await _show(
            target,
            "🎁 <b>PRESENTES</b>\n\nNenhum presente disponível no momento. "
            "Tente novamente mais tarde.",
            InlineKeyboardMarkup(inline_keyboard=[_back_more()]),
        )
        return
    kb = InlineKeyboardBuilder()
    for idx, cat in enumerate(_categories(items)):
        count = sum(1 for i in items if i.category == cat)
        kb.row(
            InlineKeyboardButton(
                text=f"{cat} ({count})", callback_data=f"xs:gcat:{idx}:0"
            )
        )
    kb.row(*_back_more())
    await _show(
        target,
        "🎁 <b>PRESENTES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Escolha uma categoria. O presente é enviado direto para a conta "
        "do UID informado.",
        kb.as_markup(),
    )


@router.callback_query(F.data == "xs:gifts")
async def cb_gifts(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    await _ack(query)
    await state.clear()
    await _gift_categories_screen(query, config, db, ffhub_shop)


@router.message(Command("presentes"), F.chat.type == "private")
async def cmd_gifts(
    message: Message,
    state: FSMContext,
    config: Config,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    await state.clear()
    await _gift_categories_screen(message, config, db, ffhub_shop)


@router.callback_query(F.data.startswith("xs:gcat:"))
async def cb_gift_category(
    query: CallbackQuery,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    await _ack(query)
    try:
        _, _, idx_raw, page_raw = query.data.split(":")
        idx, page = int(idx_raw), int(page_raw)
    except ValueError:
        return
    items = await _available_gifts(ffhub_shop)
    cats = _categories(items)
    if not 0 <= idx < len(cats):
        await edit_screen(query, UNAVAILABLE, more_menu())
        return
    cat = cats[idx]
    cat_items = [i for i in items if i.category == cat]
    pages = max(1, (len(cat_items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = cat_items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for item in chunk:
        price, _, _ = await gift_price_for_user(
            db, item.gift_id, item.cost, query.from_user.id
        )
        kb.row(
            InlineKeyboardButton(
                text=f"{item.name} • R$ {format_price(price)}",
                callback_data=f"xs:gift:{item.gift_id}"[:64],
            )
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"xs:gcat:{idx}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"xs:gcat:{idx}:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="⬅️ Categorias", callback_data="xs:gifts"))
    await edit_screen(
        query,
        f"🎁 <b>{html.escape(cat.upper())}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Página {page + 1}/{pages}. Escolha o presente:",
        kb.as_markup(),
    )


@router.callback_query(F.data.startswith("xs:gift:"))
async def cb_gift_item(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    await _ack(query)
    gift_id = query.data.split(":", 2)[2]
    item = await ffhub_shop.gift(gift_id)
    if not item or not item.available:
        await edit_screen(query, UNAVAILABLE, more_menu())
        return
    price, is_reseller, discount = await gift_price_for_user(
        db, item.gift_id, item.cost, query.from_user.id
    )
    await state.set_state(GiftFlow.waiting_uid)
    await state.update_data(gift_id=item.gift_id)
    reseller_line = (
        f"\n🤝 Desconto revenda: <b>{discount}%</b>" if is_reseller and discount > 0 else ""
    )
    await edit_screen(
        query,
        "🎁 <b>PRESENTE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Item: <b>{html.escape(item.name)}</b>\n"
        f"🗂️ Categoria: <b>{html.escape(item.category)}</b>\n"
        f"💰 Valor: <b>R$ {format_price(price)}</b>{reseller_line}\n\n"
        "Envie agora o <b>UID</b> da conta que vai receber o presente.",
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Cancelar", callback_data="xs:gifts")]]
        ),
    )


@router.message(GiftFlow.waiting_uid, F.text.regexp(r"^(?!/).+"))
async def gift_receive_uid(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config,
    ffhub_shop: FFHubShopApi,
) -> None:
    uid = clean_game_id(message.text or "")
    if not is_valid_game_id(uid) or not 8 <= len(uid) <= 12:
        await message.answer(
            "⚠️ <b>UID inválido.</b>\nEnvie somente o ID numérico da conta."
        )
        return
    data = await state.get_data()
    await state.clear()
    item = await ffhub_shop.gift(str(data.get("gift_id") or ""))
    rp = await resolve_product(config, db, "gift")
    if not item or not item.available or not rp or not rp.service_ready:
        await message.answer(UNAVAILABLE, reply_markup=more_menu())
        return
    price, is_reseller, discount = await gift_price_for_user(
        db, item.gift_id, item.cost, message.from_user.id
    )
    balance = Decimal(await db.get_balance_cents(message.from_user.id)) / Decimal(100)
    reseller_line = (
        f"🤝 Desconto revenda: <b>{discount}%</b>\n" if is_reseller and discount > 0 else ""
    )
    ref = f"{item.gift_id}:{uid}"
    if len(f"paypix:gift:{ref}") > 64:
        await message.answer(UNAVAILABLE, reply_markup=more_menu())
        return
    await message.answer(
        "🧾 <b>CONFIRMAR PEDIDO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Presente: <b>{html.escape(item.name)}</b>\n"
        f"🆔 UID: <code>{uid}</code>\n"
        f"{reseller_line}"
        f"💰 Valor: <b>R$ {format_price(price)}</b>\n"
        f"👛 Seu saldo: <b>R$ {format_price(balance)}</b>\n\n"
        "⚠️ Confira o UID: o envio é feito para a conta informada.\n\n"
        "Escolha como deseja pagar:",
        reply_markup=checkout_menu("gift", ref_value=ref, allow_balance=True),
    )


# ---------------------------------------------------------------------------
# Diamantes
# ---------------------------------------------------------------------------
async def _diamonds_screen(
    target: Message | CallbackQuery, config: Config, db: Database
) -> None:
    products = await resolve_products(config, db)
    kb = InlineKeyboardBuilder()
    buttons = []
    for qty in DIAMOND_PACKS:
        rp = products.get(diamond_code(qty))
        if not rp or not rp.available:
            continue
        price, _, _ = await price_for_user(db, rp, _user_id(target))
        buttons.append(
            InlineKeyboardButton(
                text=f"💎 {qty:,} • R$ {format_price(price)}".replace(",", "."),
                callback_data=f"xs:dpack:{qty}",
            )
        )
    if not buttons:
        await _show(
            target,
            "💎 <b>DIAMANTES</b>\n\nNenhum pacote disponível no momento. "
            "Tente novamente mais tarde.",
            InlineKeyboardMarkup(inline_keyboard=[_back_more()]),
        )
        return
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i + 2])
    kb.row(*_back_more())
    await _show(
        target,
        "💎 <b>DIAMANTES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Recarga feita com o <b>token Rewards</b> da sua conta.\n"
        "Depois do pagamento, o bot pede o token e registra o pedido.\n"
        "Você é avisado aqui quando os diamantes forem entregues.\n\n"
        "Escolha o pacote:",
        kb.as_markup(),
    )


@router.callback_query(F.data == "xs:dima")
async def cb_diamonds(query: CallbackQuery, config: Config, db: Database) -> None:
    await _ack(query)
    await _diamonds_screen(query, config, db)


@router.message(Command("diamantes"), F.chat.type == "private")
async def cmd_diamonds(message: Message, config: Config, db: Database) -> None:
    await _diamonds_screen(message, config, db)


async def _single_product_checkout(
    query: CallbackQuery,
    config: Config,
    db: Database,
    code: str,
    title: str,
    body: str,
    back: list[InlineKeyboardButton],
) -> None:
    rp = await resolve_product(config, db, code)
    if not rp or not rp.available:
        await edit_screen(query, UNAVAILABLE, InlineKeyboardMarkup(inline_keyboard=[back]))
        return
    price, is_reseller, discount = await price_for_user(db, rp, query.from_user.id)
    reseller_line = (
        f"\n🤝 Desconto revenda: <b>{discount}%</b>" if is_reseller and discount > 0 else ""
    )
    kb = checkout_menu(code, allow_balance=True)
    kb.inline_keyboard[-1] = back
    await edit_screen(
        query,
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{body}\n\n"
        f"💰 Valor: <b>R$ {format_price(price)}</b>{reseller_line}\n\n"
        "Escolha como deseja pagar:",
        kb,
    )


@router.callback_query(F.data.startswith("xs:dpack:"))
async def cb_diamond_pack(query: CallbackQuery, config: Config, db: Database) -> None:
    await _ack(query)
    try:
        qty = int(query.data.split(":")[2])
    except ValueError:
        return
    if qty not in DIAMOND_PACKS:
        return
    await _single_product_checkout(
        query,
        config,
        db,
        diamond_code(qty),
        f"💎 <b>{qty:,} DIAMANTES</b>".replace(",", "."),
        "Após o pagamento, envie o seu <b>token Rewards</b> aqui no privado.\n"
        "O pedido é registrado e a entrega é acompanhada automaticamente.",
        [InlineKeyboardButton(text="⬅️ Voltar", callback_data="xs:dima")],
    )


@router.callback_query(F.data.startswith("xs:dst:"))
async def cb_diamond_status(
    query: CallbackQuery,
    db: Database,
    config: Config,
    api_orders: ApiOrders,
) -> None:
    try:
        order_id = int(query.data.split(":")[2])
    except ValueError:
        await _ack(query)
        return
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != query.from_user.id:
        await _ack(query, "Pedido não encontrado.")
        return
    try:
        status = await api_orders.check_diamond(order, notify=True)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao consultar pedido de diamantes %s", order_id)
        status = str(order["status"])
    labels = {
        "processing": "⏳ Aguardando entrega",
        "delivered": "✅ Entregue",
        "refunded": "↩️ Estornado ao saldo",
        "review": "🔎 Em análise pela equipe",
        "awaiting_id": "🔑 Aguardando token",
        "sending": "⏳ Processando",
    }
    await _ack(query, labels.get(status, "Status atualizado"))
    if status == "processing":
        try:
            await query.message.edit_reply_markup(
                reply_markup=diamond_status_button(order_id)
            )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Likes (envio único) e Snickers
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "xs:likes")
async def cb_likes_once(query: CallbackQuery, config: Config, db: Database) -> None:
    await _ack(query)
    await _single_product_checkout(
        query,
        config,
        db,
        "like2_single",
        "❤️ <b>LIKES • ENVIO ÚNICO</b>",
        "Um envio de likes para a sua conta.\n"
        "Após o pagamento, envie o <b>UID</b> e os likes são enviados na hora.",
        _back_more(),
    )


@router.callback_query(F.data == "xs:snickers")
async def cb_snickers(query: CallbackQuery, config: Config, db: Database) -> None:
    await _ack(query)
    await _single_product_checkout(
        query,
        config,
        db,
        "snickers",
        "🍫 <b>CÓDIGO SNICKERS</b>",
        "Resgate do código Snickers na sua conta.\n"
        "Após o pagamento, envie o seu <b>token Rewards</b> aqui no privado.",
        _back_more(),
    )


@router.message(Command("snickers"), F.chat.type == "private")
async def cmd_snickers(message: Message) -> None:
    await message.answer(
        "🍫 Toque abaixo para comprar o código Snickers:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🍫 Snickers", callback_data="xs:snickers")],
                [InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:v2:home")],
            ]
        ),
    )


# ---------------------------------------------------------------------------
# Comandos do dono
# ---------------------------------------------------------------------------
def _is_admin(message: Message, config: Config) -> bool:
    return bool(message.from_user) and message.from_user.id in config.admin_ids


def _parse_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.strip().replace("R$", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


@router.message(Command("margem"))
async def cmd_markup(message: Message, config: Config, db: Database) -> None:
    if not _is_admin(message, config):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        current = await gift_markup_percent(db)
        await message.reply(
            f"🎁 Margem atual dos presentes: <b>{current}%</b> sobre o custo.\n"
            "Use: <code>/margem 30</code>"
        )
        return
    value = _parse_decimal(parts[1].replace("%", ""))
    if value is None or not Decimal("0") <= value <= Decimal("500"):
        await message.reply("⚠️ Informe uma margem entre 0 e 500.")
        return
    await db.set_owner_setting("GIFT_MARKUP_PERCENT", str(value))
    await message.reply(f"✅ Margem dos presentes salva: <b>{value}%</b>.")


@router.message(Command("precopresente"))
async def cmd_gift_price(
    message: Message, config: Config, db: Database, ffhub_shop: FFHubShopApi
) -> None:
    if not _is_admin(message, config):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.reply(
            "Use: <code>/precopresente ID 9,90</code>\n"
            "Para voltar à margem automática: <code>/precopresente ID reset</code>\n"
            "Veja os IDs em <code>/catalogo</code>."
        )
        return
    gift_id = parts[1]
    if parts[2].lower() == "reset":
        await db.set_owner_setting(f"GIFT_PRICE_{gift_id}", "")
        await message.reply(f"✅ Presente <code>{html.escape(gift_id)}</code> voltou à margem automática.")
        return
    value = _parse_decimal(parts[2])
    if value is None or value <= 0:
        await message.reply("⚠️ Preço inválido.")
        return
    item = await ffhub_shop.gift(gift_id)
    warn = ""
    if item and value < item.cost:
        warn = f"\n⚠️ Abaixo do custo (R$ {format_price(item.cost)})."
    await db.set_owner_setting(f"GIFT_PRICE_{gift_id}", f"{value:.2f}")
    await message.reply(
        f"✅ Preço do presente <code>{html.escape(gift_id)}</code>: "
        f"<b>R$ {format_price(value)}</b>{warn}"
    )


@router.message(Command("catalogo"))
async def cmd_catalog(
    message: Message, config: Config, db: Database, ffhub_shop: FFHubShopApi
) -> None:
    if not _is_admin(message, config):
        return
    items = await ffhub_shop.gift_catalog(force=True)
    if not items:
        await message.reply("⚠️ Não consegui carregar o catálogo de presentes agora.")
        return
    lines = ["🎁 <b>CATÁLOGO DE PRESENTES</b>", "ID · item · custo → venda", ""]
    for item in items:
        price, _, _ = await gift_price_for_user(db, item.gift_id, item.cost, 0)
        flag = "✅" if item.available else "⛔"
        stock = f" · estoque {item.stock}" if item.stock is not None else ""
        lines.append(
            f"{flag} <code>{html.escape(item.gift_id)}</code> · {html.escape(item.name)} · "
            f"R$ {format_price(item.cost)} → <b>R$ {format_price(price)}</b>{stock}"
        )
    text = "\n".join(lines)
    for i in range(0, len(text), 3800):
        await message.answer(text[i:i + 3800])


@router.message(Command("saldoapi"))
async def cmd_api_balance(
    message: Message, config: Config, ffhub_shop: FFHubShopApi
) -> None:
    if not _is_admin(message, config):
        return
    if not ffhub_shop.configured:
        await message.reply(
            "⚠️ Chave da API de produtos não configurada.\n"
            "Use no privado: <code>/setkey ffhub SUA_CHAVE</code> "
            "ou defina <code>FFHUB_API_KEY</code> nas variáveis da Square Cloud."
        )
        return
    result = await ffhub_shop.balance()
    status = await ffhub_shop.api_status()
    if not result.ok:
        await message.reply(f"⚠️ Falha ao consultar saldo: <code>{html.escape(admin_error(result))}</code>")
        return
    data = result.data
    saldo = data.get("saldo", data.get("balance"))
    if saldo is None and isinstance(data.get("conta"), dict):
        saldo = data["conta"].get("saldo")
    await message.reply(
        "💼 <b>CONTA DE PRODUTOS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Serviço: <b>{'online' if status.ok else 'sem resposta'}</b>\n"
        f"💰 Saldo: <b>{html.escape(str(saldo if saldo is not None else '—'))}</b>\n\n"
        f"<code>{html.escape(str(data)[:1500])}</code>"
    )


@router.message(Command("reembolsar"))
async def cmd_refund(
    message: Message, config: Config, db: Database, api_orders: ApiOrders, bot: Bot
) -> None:
    if not _is_admin(message, config):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("#").isdigit():
        await message.reply("Use: <code>/reembolsar NUMERO_DO_PEDIDO</code> (devolve ao saldo do bot)")
        return
    order = await db.get_order(int(parts[1].lstrip("#")))
    if not order:
        await message.reply("⚠️ Pedido não encontrado.")
        return
    if order["status"] in {"refunded", "delivered", "pending", "expired"}:
        await message.reply(f"⚠️ Pedido está como <b>{order['status']}</b>; nada foi feito.")
        return
    balance = await api_orders.refund_to_wallet(order, "estorno manual pelo dono")
    try:
        await bot.send_message(
            int(order["user_id"]),
            "↩️ <b>Pedido estornado</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 Pedido: <code>#{order['id']}</code>\n"
            f"💰 R$ {format_price(Decimal(str(order['amount'])))} devolvidos ao seu saldo.\n"
            f"👛 Saldo atual: <b>R$ {format_price(balance)}</b>",
            reply_markup=back_home(),
        )
    except Exception:  # noqa: BLE001
        pass
    await message.reply(f"✅ Pedido #{order['id']} estornado ao saldo do cliente.")


@router.message(Command("concluir"))
async def cmd_complete(
    message: Message, config: Config, db: Database, bot: Bot
) -> None:
    if not _is_admin(message, config):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("#").isdigit():
        await message.reply("Use: <code>/concluir NUMERO_DO_PEDIDO</code>")
        return
    order = await db.get_order(int(parts[1].lstrip("#")))
    if not order:
        await message.reply("⚠️ Pedido não encontrado.")
        return
    if order["status"] in {"delivered", "refunded", "pending", "expired"}:
        await message.reply(f"⚠️ Pedido está como <b>{order['status']}</b>; nada foi feito.")
        return
    await db.update_order_status(int(order["id"]), "delivered", "concluído manualmente")
    try:
        await bot.send_message(
            int(order["user_id"]),
            "✅ <b>Pedido concluído</b>\n"
            f"📄 Pedido: <code>#{order['id']}</code>\n\n"
            "Obrigado pela compra! 💚",
            reply_markup=back_home(),
        )
    except Exception:  # noqa: BLE001
        pass
    await message.reply(f"✅ Pedido #{order['id']} marcado como entregue.")
