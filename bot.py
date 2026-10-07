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
from aiogram.types import BotCommand

from app.config import load_config
from app.database import Database
from app.delivery import DeliveryService
from app.handlers import register_handlers
from app.services.autolike import AutoLikeApi
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.likes import LikesApi
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
    await db.init_product_settings(config.products)

    autolike = AutoLikeApi(
        config.api_base_url,
        config.autolike_api_key,
        config.default_region,
    )
    likes_api = LikesApi(
        config.likes_api_base_url,
        config.likes_api_key,
        config.likes_quantity,
    )
    autosystem_likes_api = AutoSystemLikesApi(
        config.autosystem_base_url,
        config.autosystem_api_key,
        config.likes_quantity,
        config.default_region,
    )

    quota_check = await likes_api.quota()
    if quota_check.ok:
        logger.info(
            "Likes API OK | quota restante=%s | limite diario=%s | por requisicao=%s",
            quota_check.data.get("remaining"),
            quota_check.data.get("daily_limit"),
            quota_check.data.get("per_request_limit"),
        )
    else:
        logger.warning(
            "Likes API quota check falhou | status=%s | erro=%s",
            quota_check.status,
            quota_check.error,
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
    dp["likes_api"] = likes_api
    dp["autosystem_likes_api"] = autosystem_likes_api
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
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Abrir menu principal"),
                BotCommand(command="menu", description="Abrir menu principal"),
                BotCommand(command="like", description="Enviar likes por ID"),
            ]
        )
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
