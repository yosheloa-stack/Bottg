"""Atendente de suporte com IA (Square Cloud AI Gateway).

A IA consulta os dados internos do bot por meio de ferramentas (tool calling):
pedidos, pagamentos, entregas, assinaturas de Auto Like, saldo e falhas.

Escopo de acesso:
- Cliente: só enxerga os próprios pedidos e dados. Qualquer ID de pedido,
  pagamento ou usuário de outra pessoa é recusado pela ferramenta.
- Dono/admin: consulta qualquer pedido, usuário, UID ou falha e pode
  reprocessar a entrega de um pedido.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from typing import Any

from aiogram import Bot

from app.catalog import resolve_products
from app.config import Config
from app.database import Database
from app.delivery import DeliveryService
from app.services.payments import PaymentGateway, PaymentStatus
from app.services.support_ai import SquareCloudAI
from app.utils import format_price

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_TOOL_RESULT_CHARS = 3500
HISTORY_LIMIT = 12

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("America/Sao_Paulo")
except Exception:  # noqa: BLE001
    _TZ = None

STATUS_INFO = {
    "pending": "aguardando pagamento PIX",
    "paid": "pago, entrega em andamento",
    "awaiting_id": "pago, aguardando o cliente informar o UID do Free Fire",
    "active": "assinatura ativa (envios diários em andamento)",
    "delivered": "entregue/concluído",
    "failed": "pago, mas a entrega falhou",
    "expired": "PIX expirou sem pagamento",
}

AI_ERROR_HINTS = {
    "access_denied": "A chave é inválida, foi revogada ou expirou. Use a API key da conta Square Cloud (não a chave S3 que começa com SQ2_) com /setkey ia.",
    "missing_scope": "A API key não tem o escopo ai:chat. Gere uma chave com esse escopo no painel da Square Cloud.",
    "upgrade_required": "O plano da conta não inclui o AI Gateway (Hobby/sem plano não tem acesso).",
    "daily_limit_exceeded": "Limite diário de tokens da IA atingido; volta às 21h (00:00 UTC).",
    "weekly_limit_exceeded": "Limite semanal de tokens da IA atingido; volta domingo às 21h (segunda 00:00 UTC).",
    "daily_capacity_reached": "A Square Cloud atingiu a capacidade diária da IA; tente mais tarde.",
    "HTTP 401": "A chave foi recusada. Use a API key da conta Square Cloud (não a chave S3 que começa com SQ2_) com /setkey ia.",
    "rate_limit_exceeded": "Muitas mensagens seguidas; aguarde alguns segundos.",
    "concurrent_limit_reached": "Outra conversa está usando a IA agora; tente de novo em instantes.",
}

SYSTEM_PROMPT = """Você é a atendente de suporte do bot de vendas de Free Fire "{bot_name}" no Telegram.
Responda sempre em {language}, de forma curta, simpática e objetiva (no máximo uns 8 linhas).
Não use HTML nem Markdown; escreva texto simples, sem emojis.

Produtos vendidos: Auto Like 220 e Auto Like 500 (likes diários por X dias), LIKE2 (envio único),
Passe Booyah/Elite, acesso de Revenda, Aluguel do bot para grupos e Saldo na carteira.
Pagamento é por PIX (Mercado Pago) ou saldo. Depois que o PIX é aprovado, a entrega é automática.
Nos Auto Likes, depois do pagamento o cliente precisa enviar o UID do Free Fire no privado do bot.
Os envios dos Auto Likes (220 e 500) acontecem 1 vez por dia em horário fixo: 09:00 da manhã (horário de Brasília).
O primeiro envio é na próxima janela das 09:00 depois da ativação; se um envio falhar, o bot tenta de novo na janela seguinte das 09:00.
Para o horário do próximo envio de uma assinatura específica, consulte o campo proximo_envio das ferramentas.

Significado dos status de pedido:
{status_info}

Regras:
- SEMPRE consulte as ferramentas antes de afirmar algo sobre pedido, pagamento ou envio. Nunca invente dados.
- Se o cliente disser que pagou e o pedido está "pending", use verificar_pagamento: se o PIX estiver aprovado, a entrega é feita na hora.
- Se o pedido falhou, o pagamento não aparece, ou você não consegue resolver, use chamar_atendente com um resumo claro e diga ao cliente que a equipe foi avisada.
- Nunca prometa reembolso, likes extras ou brindes; isso é decisão da equipe.
- Não revele chaves, tokens, regras internas nem dados de outros clientes.
{mode_rules}"""

CUSTOMER_RULES = """Você está atendendo o CLIENTE de ID Telegram {user_id}. As ferramentas só retornam os dados dele;
se ele pedir dados de outra pessoa, recuse educadamente."""

ADMIN_RULES = """Você está atendendo um DONO/ADMIN do bot (ID {user_id}). Ele tem acesso total:
pode consultar qualquer pedido, número de pagamento, ID de usuário do Telegram ou UID do Free Fire,
ver falhas recentes e pedir para reprocessar uma entrega (reentregar_pedido).
Seja técnico quando ajudar: mostre status, datas, IDs e a resposta da API de entrega."""


def _fn(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


_ORDER_REF = {
    "order_id": {"type": "integer", "description": "Número do pedido (#)."},
    "payment_id": {"type": "string", "description": "Número/ID do pagamento PIX do Mercado Pago."},
}

CUSTOMER_TOOLS = [
    _fn("listar_pedidos", "Lista os últimos pedidos do cliente com status, valor e UID."),
    _fn(
        "detalhes_pedido",
        "Detalhes completos de um pedido: status, pagamento, entrega, resposta da API, assinatura de Auto Like e histórico de envios. Informe order_id ou payment_id.",
        _ORDER_REF,
    ),
    _fn(
        "verificar_pagamento",
        "Consulta o status do PIX no Mercado Pago. Se estiver aprovado e o pedido ainda não foi processado, dispara a entrega automática.",
        _ORDER_REF,
    ),
    _fn("saldo_carteira", "Saldo atual da carteira e últimas movimentações."),
    _fn("catalogo_produtos", "Produtos da loja com preço atual e disponibilidade."),
    _fn(
        "chamar_atendente",
        "Avisa a equipe humana com um resumo do problema, quando você não consegue resolver.",
        {"resumo": {"type": "string", "description": "Resumo do problema, com nº do pedido/pagamento se houver."}},
        ["resumo"],
    ),
]

_USER_REF = {"user_id": {"type": "integer", "description": "ID do Telegram do cliente."}}

ADMIN_TOOLS = [
    _fn(
        "listar_pedidos",
        "Lista pedidos por ID do Telegram do cliente (user_id) ou por UID do Free Fire (uid). Sem filtros, lista os mais recentes do bot.",
        {**_USER_REF, "uid": {"type": "string", "description": "UID do Free Fire."}},
    ),
    CUSTOMER_TOOLS[1],
    CUSTOMER_TOOLS[2],
    _fn("saldo_carteira", "Saldo e movimentações da carteira de um cliente.", _USER_REF, ["user_id"]),
    _fn(
        "info_usuario",
        "Dados de um cliente: nome, username, VIP, VIP2, revenda, saldo e assinaturas de Auto Like.",
        _USER_REF,
        ["user_id"],
    ),
    _fn(
        "assinaturas_uid",
        "Assinaturas de Auto Like de um UID do Free Fire com histórico dos últimos envios.",
        {"uid": {"type": "string", "description": "UID do Free Fire."}},
        ["uid"],
    ),
    _fn("falhas_recentes", "Pedidos com entrega falhada e assinaturas de Auto Like com erro recente."),
    _fn(
        "reentregar_pedido",
        "Reprocessa a entrega de um pedido pago (PIX aprovado ou pago com saldo) que está pendente, pago ou falhou. Não reenvia pedidos já entregues.",
        {"order_id": {"type": "integer", "description": "Número do pedido (#)."}},
        ["order_id"],
    ),
    CUSTOMER_TOOLS[4],
]


def _fmt_ts(value: Any) -> str | None:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, _TZ).strftime("%d/%m/%Y %H:%M")


def _short(value: Any, limit: int = 400) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


class SupportAgent:
    def __init__(
        self,
        ai: SquareCloudAI,
        bot: Bot,
        db: Database,
        config: Config,
        gateway: PaymentGateway,
        delivery: DeliveryService,
    ) -> None:
        self.ai = ai
        self.bot = bot
        self.db = db
        self.config = config
        self.gateway = gateway
        self.delivery = delivery

    @property
    def configured(self) -> bool:
        return self.ai.configured

    def is_admin(self, user_id: int) -> bool:
        return int(user_id) in self.config.admin_ids

    # ---------- Conversa ----------
    async def answer(
        self,
        user_id: int,
        history: list[dict[str, str]],
        text: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Responde ao cliente. Retorna (resposta, histórico atualizado)."""
        admin = self.is_admin(user_id)
        me = await self.bot.get_me()
        try:
            lang = await self.db.get_user_language(user_id)
        except Exception:  # noqa: BLE001
            lang = None
        system = SYSTEM_PROMPT.format(
            language={"es": "espanhol", "en": "inglês"}.get(lang or "", "português do Brasil"),
            bot_name=me.full_name or me.username or "Bot",
            status_info="\n".join(f"- {k}: {v}" for k, v in STATUS_INFO.items()),
            mode_rules=(ADMIN_RULES if admin else CUSTOMER_RULES).format(user_id=user_id),
        )
        tools = ADMIN_TOOLS if admin else CUSTOMER_TOOLS

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history[-HISTORY_LIMIT:])
        messages.append({"role": "user", "content": text})

        reply = ""
        for _ in range(MAX_TOOL_ROUNDS + 1):
            result = await self.ai.chat(messages, tools)
            if not result.ok:
                logger.warning("IA de suporte indisponível: %s", result.error)
                reply = (
                    "⚠️ O atendimento automático está indisponível agora. "
                    f"Fale com a equipe: {self.config.support_username}"
                )
                if admin:
                    hint = AI_ERROR_HINTS.get(result.error, "")
                    key = self.ai._key
                    masked = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "?"
                    reply += (
                        f"\n\n(dono) Erro da IA: {result.error} · chave em uso {masked}"
                        + (f"\n{hint}" if hint else "")
                    )
                break

            message = result.message
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                reply = (message.get("content") or "").strip()
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = call.get("function") or {}
                output = await self._run_tool(
                    user_id, admin, fn.get("name") or "", fn.get("arguments") or "{}"
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": output,
                    }
                )

        if not reply:
            reply = (
                "Não consegui concluir a análise agora. "
                f"Se precisar, fale com a equipe: {self.config.support_username}"
            )

        new_history = list(history) + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ]
        return reply, new_history[-HISTORY_LIMIT:]

    # ---------- Ferramentas ----------
    async def _run_tool(self, user_id: int, admin: bool, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args) if raw_args else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}

        allowed = {t["function"]["name"] for t in (ADMIN_TOOLS if admin else CUSTOMER_TOOLS)}
        if name not in allowed:
            data: Any = {"erro": "Ferramenta não disponível."}
        else:
            try:
                data = await getattr(self, f"_tool_{name}")(user_id, admin, args)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Erro na ferramenta de suporte %s", name)
                data = {"erro": f"Falha ao consultar: {exc}"}

        logger.info("Suporte IA | user=%s admin=%s tool=%s args=%s", user_id, admin, name, args)
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "…(cortado)"
        return text

    def _order_summary(self, order: dict, *, full: bool = False) -> dict:
        product = self.config.products.get(order.get("product_code") or "")
        data = {
            "pedido": order.get("id"),
            "produto": product.title if product else order.get("product_code"),
            "uid_free_fire": order.get("game_id") or None,
            "valor": f"R$ {format_price(float(order.get('amount') or 0))}",
            "status": order.get("status"),
            "significado_status": STATUS_INFO.get(order.get("status") or "", ""),
            "pago_com": "saldo" if order.get("paid_with") == "balance" else "PIX",
            "pagamento_id": order.get("payment_id"),
            "dias": int(order.get("plan_days") or 0) or None,
            "criado_em": _fmt_ts(order.get("created_at")),
            "atualizado_em": _fmt_ts(order.get("updated_at")),
        }
        if full:
            data["cliente_telegram_id"] = order.get("user_id")
            data["resposta_entrega"] = _short(order.get("delivery_info"))
        return {k: v for k, v in data.items() if v not in (None, "")}

    async def _find_order(self, user_id: int, admin: bool, args: dict) -> dict | str:
        order = None
        if args.get("order_id"):
            try:
                order = await self.db.get_order(int(str(args["order_id"]).lstrip("#")))
            except ValueError:
                return "Número de pedido inválido."
        elif args.get("payment_id"):
            order = await self.db.get_order_by_payment(str(args["payment_id"]).strip())
        else:
            return "Informe o número do pedido ou do pagamento."
        if not order or (not admin and int(order["user_id"]) != int(user_id)):
            return "Pedido não encontrado para este cliente."
        return order

    async def _subscriptions_with_history(self, subs: list[dict]) -> list[dict]:
        out = []
        for sub in subs:
            history = await self.db.autolike_history(int(sub["id"]), limit=5)
            out.append(
                {
                    "assinatura": sub["id"],
                    "pedido": sub.get("order_id"),
                    "uid_free_fire": sub.get("game_id"),
                    "status": sub.get("status"),
                    "envios_feitos": f"{sub.get('sends_done')}/{sub.get('days_total')}",
                    "proximo_envio": _fmt_ts(sub.get("next_send_at")),
                    "ultimo_envio": _fmt_ts(sub.get("last_sent_at")),
                    "ultimo_resultado": _short(sub.get("last_result"), 300),
                    "servico": sub.get("provider"),
                    "historico": [
                        {
                            "data": _fmt_ts(h.get("sent_at")),
                            "nick": h.get("nickname"),
                            "likes_antes": h.get("likes_before"),
                            "likes_depois": h.get("likes_after"),
                            "enviados": h.get("likes_sent"),
                        }
                        for h in history
                    ],
                }
            )
        return out

    async def _tool_listar_pedidos(self, user_id: int, admin: bool, args: dict) -> Any:
        if not admin:
            orders = await self.db.orders_by_user(user_id)
        elif args.get("user_id"):
            orders = await self.db.orders_by_user(int(args["user_id"]))
        elif args.get("uid"):
            orders = await self.db.orders_by_game_id(str(args["uid"]).strip())
        else:
            orders = await self.db.recent_orders(limit=15)
        if not orders:
            return {"pedidos": [], "obs": "Nenhum pedido encontrado."}
        return {"pedidos": [self._order_summary(o, full=admin) for o in orders]}

    async def _tool_detalhes_pedido(self, user_id: int, admin: bool, args: dict) -> Any:
        order = await self._find_order(user_id, admin, args)
        if isinstance(order, str):
            return {"erro": order}
        subs = await self.db.autolike_subscriptions_for(order_id=int(order["id"]))
        return {
            "pedido": self._order_summary(order, full=True),
            "assinaturas_auto_like": await self._subscriptions_with_history(subs),
        }

    async def _tool_verificar_pagamento(self, user_id: int, admin: bool, args: dict) -> Any:
        order = await self._find_order(user_id, admin, args)
        if isinstance(order, str):
            return {"erro": order}
        if order.get("paid_with") == "balance":
            return {"pedido": order["id"], "obs": "Pedido pago com saldo, não há PIX.", "status": order["status"]}
        if not order.get("payment_id"):
            return {"pedido": order["id"], "erro": "Pedido sem PIX gerado."}

        status = await self.gateway.get_status(str(order["payment_id"]))
        result: dict[str, Any] = {
            "pedido": order["id"],
            "pagamento_id": order["payment_id"],
            "status_mercado_pago": status.value,
            "status_pedido": order["status"],
        }
        if status == PaymentStatus.APPROVED and order["status"] in ("pending", "expired"):
            ok = await self.delivery.fulfill_order(int(order["id"]))
            refreshed = await self.db.get_order(int(order["id"])) or order
            result["acao"] = "Pagamento aprovado: entrega disparada agora."
            result["entrega_ok"] = ok
            result["status_pedido"] = refreshed["status"]
        return result

    async def _tool_saldo_carteira(self, user_id: int, admin: bool, args: dict) -> Any:
        target = int(args["user_id"]) if admin and args.get("user_id") else user_id
        cents = await self.db.get_balance_cents(target)
        txs = await self.db.wallet_transactions(target, limit=8)
        return {
            "saldo": f"R$ {format_price(cents / 100)}",
            "movimentacoes": [
                {
                    "data": _fmt_ts(t.get("created_at")),
                    "valor": f"R$ {format_price(int(t.get('amount_cents') or 0) / 100)}",
                    "tipo": t.get("kind"),
                    "pedido": t.get("order_id"),
                    "descricao": t.get("description"),
                }
                for t in txs
            ],
        }

    async def _tool_catalogo_produtos(self, user_id: int, admin: bool, args: dict) -> Any:
        products = await resolve_products(self.config, self.db)
        return {
            "produtos": [
                {
                    "codigo": p.code,
                    "nome": p.title,
                    "preco": f"R$ {format_price(p.price)}",
                    "estoque": p.stock_label,
                    "disponivel": p.available,
                    **({"problema": p.service_issue} if admin and p.service_issue else {}),
                }
                for p in products.values()
            ]
        }

    async def _tool_chamar_atendente(self, user_id: int, admin: bool, args: dict) -> Any:
        resumo = str(args.get("resumo") or "").strip()[:1500] or "(sem resumo)"
        user = await self.db.get_user(user_id) or {}
        username = str(user.get("username") or "").strip()
        who = f"@{html.escape(username)}" if username else html.escape(str(user.get("first_name") or ""))
        text = (
            "🆘 <b>SUPORTE: CLIENTE PRECISA DE AJUDA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Cliente: {who} (<code>{user_id}</code>)\n\n"
            f"📝 {html.escape(resumo)}"
        )
        sent = 0
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
                sent += 1
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao avisar admin %s do suporte", admin_id)
        return {"equipe_avisada": sent > 0}

    async def _tool_info_usuario(self, user_id: int, admin: bool, args: dict) -> Any:
        target = int(args["user_id"])
        user = await self.db.get_user(target)
        vip = await self.db.get_vip(target)
        vip2 = await self.db.get_vip2(target)
        subs = await self.db.autolike_subscriptions_for(user_id=target, limit=5)
        return {
            "usuario": {
                "telegram_id": target,
                "username": (user or {}).get("username"),
                "nome": (user or {}).get("first_name"),
                "cadastrado_em": _fmt_ts((user or {}).get("created_at")),
            }
            if user
            else "Usuário nunca iniciou o bot.",
            "vip_ate": _fmt_ts(vip.get("expires_at")) if vip else None,
            "vip2_ate": _fmt_ts(vip2.get("expires_at")) if vip2 else None,
            "revenda": await self.db.is_reseller(target),
            "saldo": f"R$ {format_price(await self.db.get_balance_cents(target) / 100)}",
            "assinaturas_auto_like": await self._subscriptions_with_history(subs),
        }

    async def _tool_assinaturas_uid(self, user_id: int, admin: bool, args: dict) -> Any:
        subs = await self.db.autolike_subscriptions_for(game_id=str(args.get("uid") or "").strip())
        return {"assinaturas": await self._subscriptions_with_history(subs)}

    async def _tool_falhas_recentes(self, user_id: int, admin: bool, args: dict) -> Any:
        orders = await self.db.failed_orders(limit=10)
        subs = await self.db.failed_autolike_subscriptions(limit=10)
        return {
            "pedidos_com_falha": [self._order_summary(o, full=True) for o in orders],
            "auto_likes_com_erro": [
                {
                    "assinatura": s["id"],
                    "pedido": s.get("order_id"),
                    "cliente_telegram_id": s.get("user_id"),
                    "uid_free_fire": s.get("game_id"),
                    "status": s.get("status"),
                    "envios_feitos": f"{s.get('sends_done')}/{s.get('days_total')}",
                    "ultimo_resultado": _short(s.get("last_result"), 300),
                    "atualizado_em": _fmt_ts(s.get("updated_at")),
                }
                for s in subs
            ],
        }

    async def _tool_reentregar_pedido(self, user_id: int, admin: bool, args: dict) -> Any:
        order = await self._find_order(user_id, admin, args)
        if isinstance(order, str):
            return {"erro": order}
        if order["status"] == "delivered":
            return {"pedido": order["id"], "obs": "Pedido já entregue; nada a fazer."}
        if order.get("paid_with") != "balance":
            if not order.get("payment_id"):
                return {"pedido": order["id"], "erro": "Pedido sem PIX gerado."}
            status = await self.gateway.get_status(str(order["payment_id"]))
            if status != PaymentStatus.APPROVED:
                return {
                    "pedido": order["id"],
                    "erro": f"PIX não aprovado no Mercado Pago ({status.value}); entrega não feita.",
                }
        ok = await self.delivery.fulfill_order(int(order["id"]))
        refreshed = await self.db.get_order(int(order["id"])) or order
        return {
            "pedido": order["id"],
            "entrega_ok": ok,
            "status_pedido": refreshed["status"],
            "resposta_entrega": _short(refreshed.get("delivery_info")),
        }
