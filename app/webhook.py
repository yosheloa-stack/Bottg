"""Servidor HTTP para notificações do gateway de pagamento.

Ao receber uma notificação válida, confirma o status diretamente no gateway
antes de disparar a entrega automática do pedido.
"""
from __future__ import annotations

import logging

from aiohttp import web

from app.config import Config
from app.database import Database
from app.delivery import DeliveryService
from app.services.payments.base import PaymentGateway, PaymentStatus

logger = logging.getLogger(__name__)


def build_webhook_app(
    config: Config,
    db: Database,
    gateway: PaymentGateway,
    delivery: DeliveryService,
) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def payment_webhook(request: web.Request) -> web.Response:
        query = dict(request.query)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}

        payment_id = await gateway.parse_webhook(query, body)
        if not payment_id:
            # Notificação irrelevante (ex: merchant_order) — responde 200 mesmo assim
            return web.json_response({"received": True})

        logger.info("Webhook de pagamento recebido para %s", payment_id)

        order = await db.get_order_by_payment(payment_id)
        if not order:
            logger.warning("Webhook: pedido não encontrado para pagamento %s", payment_id)
            return web.json_response({"received": True})

        if order["status"] == "delivered":
            return web.json_response({"received": True, "already": True})

        status = await gateway.get_status(payment_id)
        if status == PaymentStatus.APPROVED:
            await delivery.fulfill_order(order["id"])
        else:
            logger.info(
                "Pagamento %s ainda não aprovado (status=%s)", payment_id, status.value
            )

        return web.json_response({"received": True})

    app.router.add_get("/health", health)
    app.router.add_post(config.payment_webhook_path, payment_webhook)
    app.router.add_get(config.payment_webhook_path, payment_webhook)
    return app


async def start_webhook_server(app: web.Application, config: Config) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.webhook_host, config.webhook_port)
    await site.start()
    logger.info(
        "Webhook server ouvindo em %s:%s%s",
        config.webhook_host,
        config.webhook_port,
        config.payment_webhook_path,
    )
    return runner
