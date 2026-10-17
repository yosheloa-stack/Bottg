"""Backup automático do banco SQLite no Telegram.

O deploy da Square Cloud substitui os arquivos da aplicação e apaga o
data/bot.db, levando saldo, pedidos, estoque e chaves. Para não perder nada:

- A cada poucos minutos, se o banco mudou, o bot envia uma cópia para o
  privado do primeiro dono e FIXA essa mensagem (a anterior é apagada).
- Ao iniciar sem banco (ou com banco vazio), o bot procura a mensagem fixada
  no privado dos donos e restaura o arquivo antes de abrir o banco.
- /backup força um backup na hora; responder um arquivo .db com /restaurar
  troca o banco e reinicia o bot (AUTORESTART da Square Cloud).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from app.config import Config
from app.database import Database

logger = logging.getLogger(__name__)

router = Router(name="backup")

BACKUP_PREFIX = "bottg-backup"
BACKUP_INTERVAL_SECONDS = 180
SQLITE_HEADER = b"SQLite format 3\x00"
_TZ = ZoneInfo("America/Sao_Paulo")

_last_hash: str | None = None
_last_message: tuple[int, int] | None = None
_lock = asyncio.Lock()


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == SQLITE_HEADER
    except OSError:
        return False


def _has_data(path: Path) -> bool:
    """True se o banco existe e já tem algum usuário ou pedido."""
    if not path.exists() or path.stat().st_size == 0 or not _is_sqlite(path):
        return False
    try:
        con = sqlite3.connect(str(path))
        try:
            for table in ("users", "orders"):
                try:
                    if con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                        return True
                except sqlite3.OperationalError:
                    continue
            return False
        finally:
            con.close()
    except sqlite3.Error:
        return False


async def restore_if_needed(bot: Bot, db_path: str, owner_ids: list[int]) -> bool:
    """Restaura o último backup fixado se o banco local não tiver dados."""
    path = Path(db_path)
    if _has_data(path):
        return False

    for owner_id in owner_ids:
        try:
            chat = await bot.get_chat(owner_id)
        except Exception:  # noqa: BLE001
            logger.warning("Backup: não consegui abrir o privado do dono %s", owner_id)
            continue
        pinned = chat.pinned_message
        doc = pinned.document if pinned else None
        if not doc or not (doc.file_name or "").startswith(BACKUP_PREFIX):
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".restore")
        try:
            await bot.download(doc, destination=tmp)
            if not _is_sqlite(tmp):
                logger.error("Backup fixado do dono %s não é um SQLite válido.", owner_id)
                tmp.unlink(missing_ok=True)
                continue
            if path.exists():
                path.replace(path.with_suffix(".vazio"))
            tmp.replace(path)
        except Exception:  # noqa: BLE001
            logger.exception("Backup: falha ao baixar o backup do dono %s", owner_id)
            tmp.unlink(missing_ok=True)
            continue

        logger.info(
            "Banco restaurado do backup fixado no privado do dono %s (%s).",
            owner_id,
            doc.file_name,
        )
        try:
            await bot.send_message(
                owner_id,
                "♻️ <b>Banco restaurado do último backup</b>\n"
                "Saldo, pedidos, estoque e chaves foram recuperados após a atualização.",
                disable_notification=True,
            )
        except Exception:  # noqa: BLE001
            pass
        return True

    logger.warning("Nenhum backup fixado encontrado; iniciando com banco novo.")
    return False


async def _snapshot(db: Database) -> bytes:
    """Cópia consistente do banco aberto (API de backup do SQLite)."""
    fd, tmp_name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    target = sqlite3.connect(tmp_name, check_same_thread=False)
    try:
        await db.db.backup(target)
    finally:
        target.close()
    try:
        return Path(tmp_name).read_bytes()
    finally:
        os.unlink(tmp_name)


async def backup_now(bot: Bot, db: Database, config: Config, *, force: bool = False) -> bool:
    """Envia o banco ao privado do primeiro dono e fixa a mensagem."""
    global _last_hash, _last_message
    if not config.admin_ids:
        return False

    async with _lock:
        # Nunca troca um backup bom por um banco vazio (ex.: restauração falhou).
        if not _has_data(Path(config.db_path)):
            return False
        data = await _snapshot(db)
        digest = hashlib.sha256(data).hexdigest()
        if not force and digest == _last_hash:
            return False

        owner_id = int(config.admin_ids[0])
        stamp = datetime.now(_TZ).strftime("%Y%m%d-%H%M%S")
        msg = await bot.send_document(
            owner_id,
            BufferedInputFile(data, filename=f"{BACKUP_PREFIX}-{stamp}.db"),
            caption=(
                "💾 Backup automático do banco (saldo, pedidos, estoque, chaves).\n"
                "Não desafixe nem apague: é daqui que o bot se recupera após uma atualização."
            ),
            disable_notification=True,
        )
        try:
            await bot.pin_chat_message(owner_id, msg.message_id, disable_notification=True)
        except Exception:  # noqa: BLE001
            logger.exception("Backup: não consegui fixar a mensagem do backup")

        # Apaga só o backup anterior desta mesma execução. O que estava fixado
        # antes de reiniciar fica guardado no histórico como cópia extra.
        if _last_message and _last_message[1] != msg.message_id:
            try:
                await bot.delete_message(*_last_message)
            except Exception:  # noqa: BLE001
                pass

        _last_hash = digest
        _last_message = (owner_id, msg.message_id)
        logger.info("Backup do banco enviado (%s bytes).", len(data))
        return True


async def watch_backups(bot: Bot, db: Database, config: Config) -> None:
    """Faz backup periódico sempre que o banco muda."""
    while True:
        try:
            await backup_now(bot, db, config)
        except Exception:  # noqa: BLE001
            logger.exception("Falha no backup automático do banco")
        await asyncio.sleep(BACKUP_INTERVAL_SECONDS)


# ---------- Comandos do dono ----------
@router.message(Command("backup"), F.chat.type == "private")
async def cmd_backup(message: Message, bot: Bot, db: Database, config: Config) -> None:
    if not message.from_user or message.from_user.id not in config.admin_ids:
        return
    try:
        await backup_now(bot, db, config, force=True)
        await message.answer("✅ Backup feito e fixado no privado do dono principal.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha no /backup")
        await message.answer(f"⚠️ Falha no backup: {exc}")


@router.message(Command("restaurar"), F.chat.type == "private")
async def cmd_restore(message: Message, bot: Bot, db: Database, config: Config) -> None:
    if not message.from_user or message.from_user.id not in config.admin_ids:
        return
    reply = message.reply_to_message
    doc = reply.document if reply else None
    if not doc:
        await message.answer(
            "Responda a um arquivo <code>.db</code> de backup com <code>/restaurar</code>.\n"
            "⚠️ Isso substitui TODO o banco atual pelo do arquivo."
        )
        return

    path = Path(config.db_path)
    tmp = path.with_suffix(".restore")
    await bot.download(doc, destination=tmp)
    if not _is_sqlite(tmp):
        tmp.unlink(missing_ok=True)
        await message.answer("⚠️ Esse arquivo não é um banco SQLite válido.")
        return

    # Guarda o banco atual antes de trocar.
    try:
        await backup_now(bot, db, config, force=True)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao salvar o banco atual antes de restaurar")
    await db.close()
    if path.exists():
        path.replace(path.with_name(f"{path.stem}-antes-restaurar-{int(time.time())}.db"))
    tmp.replace(path)
    await message.answer("♻️ Banco restaurado. Reiniciando o bot para carregar os dados…")
    logger.warning("Banco restaurado via /restaurar por %s; reiniciando.", message.from_user.id)
    await asyncio.sleep(1)
    os._exit(0)
