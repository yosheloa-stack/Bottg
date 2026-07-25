"""Ponto de entrada do bot.

Inicializa a configuração, o banco de dados, as APIs, o gateway de
pagamento, os handlers e o servidor de webhook, e então inicia o
long polling do Telegram.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import load_config
from app.database import Database
from app.delivery import DeliveryService
from app.handlers import register_handlers
from app.services.autolike import AutoLikeApi
from app.services.passe import PasseApi
from app.services.payments import MercadoPagoGateway
from app.webhook import build_webhook_app, start_webhook_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("bottg")


async def main() -> None:
    config = load_config()

    # Infraestrutura
    db = Database(config.db_path)
    await db.connect()

    autolike = AutoLikeApi(
        config.api_base_url, config.autolike_api_key, config.default_region
    )
    passe = PasseApi(config.api_base_url, config.passe_api_key)
    gateway = MercadoPagoGateway(
        config.mp_access_token, notification_url=config.mp_notification_url
    )

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    delivery = DeliveryService(bot, db, config, autolike, passe)

    dp = Dispatcher()
    # Injeção de dependências nos handlers
    dp["config"] = config
    dp["db"] = db
    dp["autolike"] = autolike
    dp["passe"] = passe
    dp["gateway"] = gateway
    dp["delivery"] = delivery

    register_handlers(dp)

    # Servidor de webhook (Mercado Pago)
    runner = None
    if config.webhook_public_url:
        web_app = build_webhook_app(config, db, gateway, delivery)
        runner = await start_webhook_server(web_app, config)
    else:
        logger.warning(
            "WEBHOOK_PUBLIC_URL não definido — pagamentos serão confirmados "
            "apenas via botão 'Já paguei / verificar'."
        )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info("Bot iniciado como @%s", me.username)
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Encerrando...")
