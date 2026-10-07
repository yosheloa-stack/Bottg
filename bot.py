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
from app.services.ffhub_shop import FFHubShopApi
from app.services.likes import LikesApi
from app.services.passe import PasseApi
from app.services.payments import EfiGateway, PaymentStatus
from app.webhook import build_webhook_app, start_webhook_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("bottg")


async def watch_pending_payments(
    db: Database,
    gateway: EfiGateway,
    delivery: DeliveryService,
) -> None:
    """Confirma PIX pendentes automaticamente e entrega pedidos pagos."""
    while True:
        try:
            orders = await db.pending_orders(limit=100)
            for order in orders:
                payment_id = order.get("payment_id")
                if not payment_id:
                    continue
                try:
                    status = await gateway.get_status(str(payment_id))
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Falha ao consultar PIX Efí do pedido %s", order.get("id")
                    )
                    continue

                if status == PaymentStatus.APPROVED:
                    logger.info(
                        "PIX Efí confirmado automaticamente | pedido=%s | txid=%s",
                        order.get("id"),
                        payment_id,
                    )
                    await delivery.fulfill_order(order["id"])
        except Exception:  # noqa: BLE001
            logger.exception("Erro no monitor automático de pagamentos Efí")

        await asyncio.sleep(8)


async def watch_ffhub_autolikes(
    bot: Bot,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    """Executa os envios diários das assinaturas Auto-Like Premium."""
    while True:
        try:
            due = await db.due_ffhub_autolikes(limit=50)
            for sub in due:
                sub_id = int(sub["id"])
                # Reserva antes da chamada remota. Se o processo cair durante o envio,
                # evita repetir imediatamente e gastar saldo duas vezes.
                await db.reserve_ffhub_autolike_attempt(sub_id)

                result = await ffhub_shop.send_paid_likes(str(sub["game_id"]))
                if not result.ok:
                    logger.warning(
                        "FFHub Auto-Like falhou | sub=%s uid=%s status=%s error=%s",
                        sub_id,
                        sub["game_id"],
                        result.status,
                        result.error,
                    )
                    await db.retry_ffhub_autolike_later(
                        sub_id,
                        result_text=str(result.data or result.error or ""),
                        delay_seconds=900,
                    )
                    continue

                updated = await db.complete_ffhub_autolike_send(
                    sub_id,
                    result_text=str(result.data),
                )
                if not updated:
                    continue

                data = result.data if isinstance(result.data, dict) else {}
                sent = (
                    data.get("likes_enviados")
                    or data.get("likes_sent")
                    or data.get("enviados")
                    or data.get("likes")
                    or "—"
                )
                done = int(updated["sends_done"])
                total = int(updated["days_total"])

                try:
                    await bot.send_message(
                        int(updated["user_id"]),
                        "💚 <b>Auto-Like Premium enviado!</b>\n\n"
                        f"🆔 UID: <code>{updated['game_id']}</code>\n"
                        f"❤️ Likes: <b>+{sent}</b>\n"
                        f"📆 Entrega: <b>{done}/{total}</b>",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Falha ao avisar envio FFHub da assinatura %s", sub_id
                    )

                if updated["status"] == "completed":
                    await db.update_order_status(int(updated["order_id"]), "delivered")
                    try:
                        await bot.send_message(
                            int(updated["user_id"]),
                            "🏁 <b>Plano Auto-Like Premium concluído!</b>\n\n"
                            f"🆔 UID: <code>{updated['game_id']}</code>\n"
                            f"✅ Foram concluídas <b>{total} entregas diárias</b>.",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Falha ao avisar conclusão FFHub da assinatura %s", sub_id
                        )
        except Exception:  # noqa: BLE001
            logger.exception("Erro no agendador Auto-Like Premium FFHub")

        await asyncio.sleep(10)


async def main() -> None:
    config = load_config()

    # Infraestrutura
    db = Database(config.db_path)
    await db.connect()
    await db.init_product_settings(config.products)

    owner_settings = await db.all_owner_settings()

    def private_setting(key: str, fallback: str = "") -> str:
        return (owner_settings.get(key) or fallback or "").strip()

    # Migração do preço antigo do Passe Booyah.
    # Só troca o valor legado de 19.90; preços personalizados pelo admin são preservados.
    passe_setting = await db.get_product_setting("passe")
    if passe_setting and str(passe_setting.get("price")) == "19.90":
        await db.set_price("passe", "4.00")
        logger.info("Preço do Passe Booyah migrado de R$ 19,90 para R$ 4,00.")

    autolike = AutoLikeApi(
        config.api_base_url,
        config.autolike_api_key,
        config.default_region,
    )
    likes_api = LikesApi(
        config.likes_api_base_url,
        private_setting("LIKES_API_KEY", config.likes_api_key),
        config.likes_quantity,
    )
    autosystem_likes_api = AutoSystemLikesApi(
        config.autosystem_base_url,
        private_setting("AUTOSYSTEM_API_KEY", config.autosystem_api_key),
        config.likes_quantity,
        config.default_region,
    )
    ffhub_shop = FFHubShopApi(
        config.ffhub_base_url,
        private_setting("FFHUB_API_KEY", config.ffhub_api_key),
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
    passe = PasseApi(
        config.api_base_url,
        private_setting("PASSE_API_KEY", config.passe_api_key),
    )
    gateway = EfiGateway(
        private_setting("EFI_CLIENT_ID", config.efi_client_id),
        private_setting("EFI_CLIENT_SECRET", config.efi_client_secret),
        private_setting("EFI_PIX_KEY", config.efi_pix_key),
        cert_path=config.efi_cert_path,
        cert_pem_base64=config.efi_cert_pem_base64,
        sandbox=config.efi_sandbox,
        webhook_token=config.efi_webhook_token,
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
    dp["ffhub_shop"] = ffhub_shop
    dp["passe"] = passe
    dp["gateway"] = gateway
    dp["delivery"] = delivery

    register_handlers(dp)

    # Agendador do Auto-Like Premium FFHub.
    autolike_task = None
    if ffhub_shop.configured:
        autolike_task = asyncio.create_task(
            watch_ffhub_autolikes(bot, db, ffhub_shop),
            name="ffhub-autolike-watcher",
        )
        logger.info("Agendador Auto-Like Premium FFHub iniciado.")
    else:
        logger.warning(
            "FFHub Shop ainda não configurada — preencha FFHUB_API_KEY."
        )

    # Confirmação automática: polling da própria API Efí.
    payment_task = None
    if gateway.configured:
        payment_task = asyncio.create_task(
            watch_pending_payments(db, gateway, delivery),
            name="efi-payment-watcher",
        )
        logger.info("Monitor automático de pagamentos Efí iniciado.")
    else:
        logger.warning(
            "Efí ainda não configurada — preencha EFI_CLIENT_ID, EFI_CLIENT_SECRET, "
            "EFI_PIX_KEY e o certificado PEM."
        )

    # Webhook Efí é opcional; o monitor acima já confirma pagamentos automaticamente.
    runner = None
    if config.webhook_public_url and gateway.configured:
        web_app = build_webhook_app(config, db, gateway, delivery)
        runner = await start_webhook_server(web_app, config)
        try:
            ok = await gateway.register_webhook(config.payment_notification_url)
            if ok:
                logger.info("Webhook Efí registrado com sucesso.")
            else:
                logger.warning(
                    "Webhook Efí não foi registrado; monitor automático seguirá ativo."
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Falha ao registrar webhook Efí; monitor automático seguirá ativo."
            )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Abrir menu principal"),
                BotCommand(command="menu", description="Abrir menu principal"),
                BotCommand(command="like", description="Enviar likes grátis por ID"),
                BotCommand(command="like2", description="Likes pagos: único ou Auto-Like"),
                BotCommand(command="id", description="Mostrar meu ID do Telegram"),
            ]
        )
        me = await bot.get_me()
        logger.info("Bot iniciado como @%s", me.username)
        await dp.start_polling(bot)
    finally:
        if autolike_task:
            autolike_task.cancel()
            try:
                await autolike_task
            except asyncio.CancelledError:
                pass
        if payment_task:
            payment_task.cancel()
            try:
                await payment_task
            except asyncio.CancelledError:
                pass
        if runner:
            await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Encerrando...")
