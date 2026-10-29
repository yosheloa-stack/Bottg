"""Ponto de entrada do bot.

Inicializa a configuração, o banco de dados, as APIs, o gateway de
pagamento, os handlers e o servidor de webhook, e então inicia o
long polling do Telegram.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.announcements import announce_autolike_send
from app.backup import restore_if_needed, watch_backups
from app.config import load_config
from app.database import Database
from app.delivery import DeliveryService
from app.handlers import register_handlers
from app.outgoing import OutgoingTextMiddleware, UserLanguageMiddleware
from app.services.autolike import AutoLikeApi
from app.services.autosystem_likes import AutoSystemLikesApi
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
from app.services.likes import LikesApi
from app.services.passe import PasseApi
from app.services.support_ai import SquareCloudAI
from app.support_agent import SupportAgent
from app.services.payments import MercadoPagoGateway, PaymentStatus
from app.webhook import build_webhook_app, start_webhook_server
from app.binance_pay import watch_binance_payments

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("bottg")


# Monitor automático de PIX: o webhook é o caminho principal; o polling é só
# reserva. Pedidos novos são consultados com frequência, os antigos bem menos,
# e depois de PENDING_MAX_AGE o pedido é marcado como expirado e sai da fila.
# Um pagamento atrasado ainda é entregue pelo webhook ou pelo botão "Já paguei".
PENDING_POLL_FAST_WINDOW = 30 * 60
PENDING_POLL_FAST_INTERVAL = 20
PENDING_POLL_SLOW_INTERVAL = 5 * 60
PENDING_MAX_AGE = 24 * 60 * 60
PENDING_CALL_SPACING = 0.5


async def watch_pending_payments(
    db: Database,
    gateway: MercadoPagoGateway,
    delivery: DeliveryService,
) -> None:
    """Confirma PIX pendentes automaticamente e entrega pedidos pagos."""
    next_check: dict[int, float] = {}
    while True:
        try:
            orders = await db.pending_orders(limit=100)
            now = time.time()
            pending_ids = {int(o["id"]) for o in orders}
            for order_id in list(next_check):
                if order_id not in pending_ids:
                    del next_check[order_id]

            for order in orders:
                if gateway.rate_limited:
                    break
                order_id = int(order["id"])
                payment_id = order.get("payment_id")
                if not payment_id:
                    continue

                age = now - int(order.get("created_at") or now)
                if age > PENDING_MAX_AGE:
                    logger.info(
                        "PIX do pedido %s sem pagamento há mais de 24h; marcado como expirado",
                        order_id,
                    )
                    await db.update_order_status(order_id, "expired")
                    continue

                if next_check.get(order_id, 0) > now:
                    continue
                next_check[order_id] = now + (
                    PENDING_POLL_FAST_INTERVAL
                    if age < PENDING_POLL_FAST_WINDOW
                    else PENDING_POLL_SLOW_INTERVAL
                )

                try:
                    status = await gateway.get_status(str(payment_id))
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Falha ao consultar PIX Mercado Pago do pedido %s", order_id
                    )
                    continue
                finally:
                    await asyncio.sleep(PENDING_CALL_SPACING)

                if status == PaymentStatus.APPROVED:
                    logger.info(
                        "PIX Mercado Pago confirmado automaticamente | pedido=%s | txid=%s",
                        order_id,
                        payment_id,
                    )
                    await delivery.fulfill_order(order_id)
                elif status in (
                    PaymentStatus.EXPIRED,
                    PaymentStatus.CANCELLED,
                    PaymentStatus.REJECTED,
                ):
                    await db.update_order_status(order_id, "expired")
        except Exception:  # noqa: BLE001
            logger.exception("Erro no monitor automático de pagamentos Mercado Pago")

        await asyncio.sleep(8)


async def watch_ffhub_autolikes(
    bot: Bot,
    db: Database,
    ffhub_shop: FFHubShopApi,
) -> None:
    """Executa os envios diários das assinaturas Auto-Like Premium."""
    while True:
        if not ffhub_shop.configured:
            await asyncio.sleep(10)
            continue

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
                    if result.status == 0:
                        # Timeout/erro de rede é ambíguo: a FFHub pode ter
                        # processado antes de a conexão cair. A reserva já deixou
                        # o próximo envio para o dia seguinte, então não repetimos
                        # agora e evitamos mandar 500–1000 likes duas vezes.
                        logger.warning(
                            "FFHub Auto-Like com resultado ambíguo; "
                            "não será repetido no mesmo dia | sub=%s",
                            sub_id,
                        )
                    else:
                        await db.retry_ffhub_autolike_later(
                            sub_id,
                            result_text=str(result.data or result.error or ""),
                            delay_seconds=900,
                        )
                    continue

                data = result.data if isinstance(result.data, dict) else {}
                parsed_like = parse_like_delivery(data)

                def _to_int(value):
                    if value is None or isinstance(value, bool):
                        return None
                    try:
                        return int(str(value).strip().replace(".", "").replace(",", ""))
                    except (TypeError, ValueError):
                        return None

                sent = _to_int(parsed_like["sent"]) or 0
                before = _to_int(parsed_like["before"])
                after = _to_int(parsed_like["after"])
                nickname = str(parsed_like["nickname"] or "Jogador")
                safe_nickname = html.escape(nickname)

                updated = await db.complete_ffhub_autolike_send(
                    sub_id,
                    result_text=str(data),
                    nickname=nickname,
                    likes_before=before,
                    likes_after=after,
                    likes_sent=sent,
                )
                if not updated:
                    continue

                stats = await db.get_ffhub_autolike_status(str(updated["game_id"]))
                total_accumulated = (
                    int(stats.get("total_likes_sent") or 0) if stats else sent
                )
                done = int(updated["sends_done"])
                total = int(updated["days_total"])

                delivery_text = (
                    "💚 <b>Auto-Like Premium enviado!</b>\n\n"
                    f"👤 Nick: <b>{safe_nickname}</b>\n"
                    f"🆔 UID: <code>{updated['game_id']}</code>\n"
                    f"❤️ Enviado hoje: <b>+{sent}</b>\n"
                    f"❤️ Total acumulado: <b>+{total_accumulated}</b>\n"
                    f"📆 Entrega: <b>{done}/{total}</b>"
                )

                try:
                    await bot.send_message(
                        int(updated["user_id"]),
                        delivery_text,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Falha ao avisar dono/cliente do Auto Like %s", sub_id
                    )

                notify_chat_id = updated.get("notify_chat_id")
                if notify_chat_id is not None:
                    group_kwargs = {}
                    notify_thread_id = updated.get("notify_thread_id")
                    if notify_thread_id is not None:
                        group_kwargs["message_thread_id"] = int(notify_thread_id)
                    try:
                        await bot.send_message(
                            int(notify_chat_id),
                            delivery_text,
                            **group_kwargs,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Falha ao avisar grupo do Auto Like %s chat=%s",
                            sub_id,
                            notify_chat_id,
                        )

                # Auto Like ativado dentro de um grupo não vai para o grupo de anúncios.
                if notify_chat_id is None:
                    try:
                        await announce_autolike_send(
                            bot,
                            db,
                            user_id=int(updated["user_id"]),
                            plan_name="Auto Like 500",
                            uid=str(updated["game_id"]),
                            nickname=nickname,
                            likes_sent=sent,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Falha ao anunciar envio Auto Like 500 sub=%s",
                            sub_id,
                        )

                if updated["status"] == "completed":
                    # Assinaturas manuais do dono usam order_id negativo e não
                    # possuem pedido PIX para atualizar.
                    if int(updated["order_id"]) > 0:
                        await db.update_order_status(
                            int(updated["order_id"]), "delivered"
                        )
                    completed_text = (
                        "🏁 <b>Plano Auto-Like Premium concluído!</b>\n\n"
                        f"👤 Nick: <b>{safe_nickname}</b>\n"
                        f"🆔 UID: <code>{updated['game_id']}</code>\n"
                        f"❤️ Total enviado: <b>+{total_accumulated}</b>\n"
                        f"✅ Foram concluídas <b>{total} entregas diárias</b>."
                    )
                    try:
                        await bot.send_message(
                            int(updated["user_id"]),
                            completed_text,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Falha ao avisar conclusão FFHub da assinatura %s", sub_id
                        )

                    if notify_chat_id is not None:
                        group_kwargs = {}
                        if notify_thread_id is not None:
                            group_kwargs["message_thread_id"] = int(notify_thread_id)
                        try:
                            await bot.send_message(
                                int(notify_chat_id),
                                completed_text,
                                **group_kwargs,
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Falha ao avisar conclusão no grupo do Auto Like %s",
                                sub_id,
                            )
        except Exception:  # noqa: BLE001
            logger.exception("Erro no agendador Auto-Like Premium FFHub")

        await asyncio.sleep(10)


async def watch_autosystem_autolikes(
    bot: Bot,
    db: Database,
    autosystem_likes_api: AutoSystemLikesApi,
) -> None:
    """Executa o Auto Like 220 diário sempre na janela das 09:00."""
    while True:
        if not autosystem_likes_api.enabled:
            await asyncio.sleep(10)
            continue

        try:
            due = await db.due_autosystem_autolikes(limit=50)
            for sub in due:
                sub_id = int(sub["id"])
                await db.reserve_ffhub_autolike_attempt(sub_id)

                result = await autosystem_likes_api.send_like(
                    str(sub["game_id"]),
                    quantity=220,
                )
                if not result.ok:
                    logger.warning(
                        "AutoSystem Auto Like 220 falhou | sub=%s uid=%s status=%s error=%s",
                        sub_id,
                        sub["game_id"],
                        result.status,
                        result.error,
                    )
                    if result.status != 0:
                        await db.retry_ffhub_autolike_later(
                            sub_id,
                            result_text=str(result.data or result.error or ""),
                            delay_seconds=900,
                        )
                    continue

                data = result.data if isinstance(result.data, dict) else {}

                def _to_int(value):
                    if value is None or isinstance(value, bool):
                        return None
                    try:
                        return int(str(value).strip().replace(".", "").replace(",", ""))
                    except (TypeError, ValueError):
                        return None

                before = _to_int(data.get("likes_before"))
                after = _to_int(data.get("likes_after"))
                sent = _to_int(data.get("likes_sent")) or 0
                confirmed = _to_int(data.get("likes_confirmed"))
                if before is not None and after is not None and after >= before:
                    sent = max(sent, after - before)
                if confirmed is not None:
                    sent = max(sent, confirmed)

                nickname = str(data.get("nickname") or "Jogador")
                updated = await db.complete_ffhub_autolike_send(
                    sub_id,
                    result_text=str(data),
                    nickname=nickname,
                    likes_before=before,
                    likes_after=after,
                    likes_sent=sent,
                )
                if not updated:
                    continue

                done = int(updated["sends_done"])
                total = int(updated["days_total"])
                delivery_text = (
                    "❤️ <b>AUTO LIKE 220 ENVIADO</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 Nick: <b>{html.escape(nickname)}</b>\n"
                    f"🆔 UID: <code>{updated['game_id']}</code>\n"
                    f"❤️ Enviado hoje: <b>+{sent}</b>\n"
                    f"📆 Entrega: <b>{done}/{total}</b>\n"
                    "⏰ Horário padrão: <b>09:00</b>"
                )

                try:
                    await bot.send_message(int(updated["user_id"]), delivery_text)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Falha ao avisar cliente do Auto Like 220 sub=%s",
                        sub_id,
                    )

                try:
                    await announce_autolike_send(
                        bot,
                        db,
                        user_id=int(updated["user_id"]),
                        plan_name="Auto Like 220",
                        uid=str(updated["game_id"]),
                        nickname=nickname,
                        likes_sent=sent,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Falha ao anunciar envio Auto Like 220 sub=%s",
                        sub_id,
                    )

                if updated["status"] == "completed":
                    if int(updated["order_id"]) > 0:
                        await db.update_order_status(
                            int(updated["order_id"]),
                            "delivered",
                        )
                    try:
                        await bot.send_message(
                            int(updated["user_id"]),
                            "🏁 <b>Auto Like 220 concluído!</b>\n\n"
                            f"🆔 UID: <code>{updated['game_id']}</code>\n"
                            f"✅ Foram concluídas <b>{total} entregas</b>.",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Falha ao avisar conclusão Auto Like 220 sub=%s",
                            sub_id,
                        )
        except Exception:  # noqa: BLE001
            logger.exception("Erro no agendador Auto Like 220")

        await asyncio.sleep(10)


async def main() -> None:
    config = load_config()
    logger.info("AURORA_BUILD=MENU_V2_2026_10_16")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # O deploy da Square Cloud apaga o data/bot.db; recupera o último backup
    # fixado no privado do dono antes de abrir o banco.
    try:
        await restore_if_needed(bot, config.db_path, config.admin_ids)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao verificar/restaurar backup do banco")

    # Infraestrutura
    db = Database(config.db_path)
    await db.connect()
    await db.init_product_settings(config.products)

    # Migração suave: se o novo Auto Like 500 ainda não tiver preço/dia,
    # aproveita o valor do antigo plano de 7 dias dividido por 7.
    try:
        new_500 = await db.get_product_setting("autolike500")
        old_7d = await db.get_product_setting("like2_7d")
        new_price = Decimal(str((new_500 or {}).get("price") or "0"))
        old_price = Decimal(str((old_7d or {}).get("price") or "0"))
        if new_price <= 0 and old_price > 0:
            daily = (old_price / Decimal("7")).quantize(Decimal("0.01"))
            if daily > 0:
                await db.set_price("autolike500", str(daily))
                logger.info(
                    "Preço/dia Auto Like 500 migrado do plano antigo: R$ %s",
                    daily,
                )
    except (InvalidOperation, ValueError):
        logger.warning("Não foi possível migrar preço antigo do Auto Like 500.")

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

    # Likes Painel fica sem chamadas automáticas; /like usa apenas Auto System.
    passe = PasseApi(
        config.api_base_url,
        private_setting("PASSE_API_KEY", config.passe_api_key),
    )
    gateway = MercadoPagoGateway(
        private_setting("MERCADOPAGO_ACCESS_TOKEN", config.mp_access_token),
        notification_url=(
            config.payment_notification_url if config.webhook_public_url else ""
        ),
    )

    delivery = DeliveryService(bot, db, config, autolike, passe)
    support_ai = SquareCloudAI(
        private_setting(
            "SQUARECLOUD_AI_KEY",
            os.getenv("SQUARECLOUD_AI_KEY") or os.getenv("SQUARECLOUD_API_KEY", ""),
        )
    )
    support_agent = SupportAgent(support_ai, bot, db, config, gateway, delivery)

    # Tudo que o bot envia sai sem emojis e no idioma do usuário.
    bot.session.middleware(OutgoingTextMiddleware(db, config.admin_ids))

    dp = Dispatcher()
    dp.update.outer_middleware(UserLanguageMiddleware(db))
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
    dp["support_ai"] = support_ai
    dp["support_agent"] = support_agent

    register_handlers(dp)

    # Agendador do Auto Like 500 fica ativo mesmo antes da chave
    # ser configurada, para aceitar /setkey ffhub sem precisar reiniciar.
    autolike_task = asyncio.create_task(
        watch_ffhub_autolikes(bot, db, ffhub_shop),
        name="ffhub-autolike-watcher",
    )
    if ffhub_shop.configured:
        logger.info("Agendador Auto Like 500 FFHub iniciado.")
    else:
        logger.warning(
            "FFHub ainda não configurada — o agendador está aguardando a chave."
        )

    autosystem_task = asyncio.create_task(
        watch_autosystem_autolikes(bot, db, autosystem_likes_api),
        name="autosystem-autolike-220-watcher",
    )
    if autosystem_likes_api.enabled:
        logger.info("Agendador Auto Like 220 iniciado para 09:00.")
    else:
        logger.warning(
            "AutoSystem ainda não configurada — o Auto Like 220 aguarda a chave."
        )

    # Confirmação automática: o watcher fica ativo mesmo antes do token
    # ser cadastrado no painel; assim a configuração passa a valer imediatamente.
    backup_task = asyncio.create_task(
        watch_backups(bot, db, config),
        name="db-backup-watcher",
    )

    payment_task = asyncio.create_task(
        watch_pending_payments(db, gateway, delivery),
        name="mercadopago-payment-watcher",
    )

    binance_task = asyncio.create_task(
        watch_binance_payments(bot, db, config, delivery),
        name="binance-email-payment-watcher",
    )
    if gateway.configured:
        logger.info("Monitor automático de pagamentos Mercado Pago iniciado.")
    else:
        logger.warning(
            "Mercado Pago ainda não configurado — informe o Access Token "
            "no painel do dono. O monitor já está aguardando a configuração."
        )

    # HTTP fica somente para health check e Mercado Pago.
    # Telegram usa polling. Antes de iniciar, removemos e confirmamos a remoção
    # de qualquer webhook antigo para evitar TelegramConflictError.
    runner = None
    if config.webhook_public_url:
        web_app = build_webhook_app(config, db, gateway, delivery)
        runner = await start_webhook_server(web_app, config)
        logger.info(
            "Webhook Mercado Pago disponível em %s",
            config.payment_notification_url,
        )

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Abrir menu principal"),
                BotCommand(command="menu", description="Abrir menu principal"),
                BotCommand(command="like", description="Enviar likes pelo grupo"),
                BotCommand(command="id", description="Mostrar meu ID do Telegram"),
                BotCommand(command="suporte", description="Falar com o suporte"),
                BotCommand(command="idioma", description="Mudar idioma do menu"),
            ]
        )
        # Descrições dos comandos para quem usa o Telegram em espanhol/inglês.
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Abrir menú principal"),
                BotCommand(command="menu", description="Abrir menú principal"),
                BotCommand(command="like", description="Enviar likes en el grupo"),
                BotCommand(command="loja", description="Ver productos y comprar"),
                BotCommand(command="divulgar", description="Agregar el bot a tu grupo"),
                BotCommand(command="id", description="Mostrar mi ID de Telegram"),
                BotCommand(command="suporte", description="Hablar con soporte"),
                BotCommand(command="idioma", description="Cambiar idioma del menú"),
            ],
            language_code="es",
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Open main menu"),
                BotCommand(command="menu", description="Open main menu"),
                BotCommand(command="like", description="Send likes in the group"),
                BotCommand(command="loja", description="View products and buy"),
                BotCommand(command="divulgar", description="Add the bot to your group"),
                BotCommand(command="id", description="Show my Telegram ID"),
                BotCommand(command="suporte", description="Talk to support"),
                BotCommand(command="language", description="Change menu language"),
            ],
            language_code="en",
        )
        me = await bot.get_me()
        logger.info("Bot iniciado como @%s", me.username)

        # Limpa webhook do Telegram com tentativas e verifica antes do polling.
        for attempt in range(1, 6):
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                info = await bot.get_webhook_info()
                if not info.url:
                    logger.info(
                        "Webhook Telegram removido. Polling liberado (tentativa %s).",
                        attempt,
                    )
                    break
                logger.warning(
                    "Webhook Telegram ainda ativo em %s após tentativa %s.",
                    info.url,
                    attempt,
                )
            except Exception:
                logger.exception(
                    "Falha ao remover webhook Telegram na tentativa %s.",
                    attempt,
                )
            await asyncio.sleep(1)
        else:
            raise RuntimeError(
                "Não foi possível remover o webhook do Telegram; polling abortado."
            )

        logger.info("Telegram em POLLING exclusivo.")
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        if autolike_task:
            autolike_task.cancel()
            try:
                await autolike_task
            except asyncio.CancelledError:
                pass
        if autosystem_task:
            autosystem_task.cancel()
            try:
                await autosystem_task
            except asyncio.CancelledError:
                pass
        if backup_task:
            backup_task.cancel()
            try:
                await backup_task
            except asyncio.CancelledError:
                pass
        if payment_task:
            payment_task.cancel()
            try:
                await payment_task
            except asyncio.CancelledError:
                pass
        binance_task.cancel()
        try:
            await binance_task
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
