"""Acesso ao banco de dados (SQLite assíncrono)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    product_code  TEXT NOT NULL,
    game_id       TEXT NOT NULL,
    amount        TEXT NOT NULL,
    payment_id    TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | delivered | failed | expired
    delivery_info TEXT,
    plan_days     INTEGER NOT NULL DEFAULT 0,
    metadata      TEXT NOT NULL DEFAULT '{}',
    paid_with     TEXT NOT NULL DEFAULT 'pix',
    payment_chat_id INTEGER,
    payment_message_id INTEGER,
    purchase_announced INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_payment_id ON orders(payment_id);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);

CREATE TABLE IF NOT EXISTS product_settings (
    code   TEXT PRIMARY KEY,
    price  TEXT,               -- preço em BRL (override do catálogo)
    stock  INTEGER NOT NULL DEFAULT -1  -- -1 = ilimitado; 0 = esgotado
);

CREATE TABLE IF NOT EXISTS like_cooldowns (
    user_id       INTEGER PRIMARY KEY,
    last_sent_at  INTEGER NOT NULL DEFAULT 0,
    lock_until    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ffhub_autolike_subscriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL UNIQUE,
    user_id       INTEGER NOT NULL,
    game_id       TEXT NOT NULL,
    days_total    INTEGER NOT NULL,
    sends_done    INTEGER NOT NULL DEFAULT 0,
    next_send_at  INTEGER NOT NULL,
    last_sent_at  INTEGER,
    status        TEXT NOT NULL DEFAULT 'active',
    last_result   TEXT,
    notify_chat_id INTEGER,
    notify_thread_id INTEGER,
    provider      TEXT NOT NULL DEFAULT 'ffhub',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ffhub_autolike_due
ON ffhub_autolike_subscriptions(status, next_send_at);

CREATE TABLE IF NOT EXISTS ffhub_autolike_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    game_id         TEXT NOT NULL,
    nickname        TEXT,
    likes_before    INTEGER,
    likes_after     INTEGER,
    likes_sent      INTEGER NOT NULL DEFAULT 0,
    raw_result      TEXT,
    sent_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ffhub_autolike_history_sub
ON ffhub_autolike_history(subscription_id, sent_at);

CREATE INDEX IF NOT EXISTS idx_ffhub_autolike_history_uid
ON ffhub_autolike_history(game_id, sent_at);

CREATE TABLE IF NOT EXISTS wallets (
    user_id       INTEGER PRIMARY KEY,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    order_id     INTEGER,
    description  TEXT,
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wallet_tx_user
ON wallet_transactions(user_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_credit_order
ON wallet_transactions(order_id)
WHERE order_id IS NOT NULL AND kind = 'credit';

CREATE TABLE IF NOT EXISTS resellers (
    user_id      INTEGER PRIMARY KEY,
    order_id     INTEGER,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_rentals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER,
    status      TEXT NOT NULL DEFAULT 'awaiting_group',
    starts_at   INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bot_rentals_user
ON bot_rentals(user_id, status, expires_at);

CREATE TABLE IF NOT EXISTS gift_cards (
    code          TEXT PRIMARY KEY,
    amount_cents  INTEGER NOT NULL,
    max_uses      INTEGER NOT NULL DEFAULT 1,
    uses_count    INTEGER NOT NULL DEFAULT 0,
    created_by    INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gift_card_redemptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL,
    user_id      INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    redeemed_at  INTEGER NOT NULL,
    UNIQUE(code, user_id)
);

CREATE INDEX IF NOT EXISTS idx_gift_card_redemptions_user
ON gift_card_redemptions(user_id, redeemed_at);

CREATE TABLE IF NOT EXISTS customer_pending_inputs (
    user_id     INTEGER PRIMARY KEY,
    action      TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    expires_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vip_users (
    user_id       INTEGER PRIMARY KEY,
    expires_at    INTEGER NOT NULL DEFAULT 0,
    added_by      INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vip_expires ON vip_users(expires_at);

CREATE TABLE IF NOT EXISTS vip2_users (
    user_id       INTEGER PRIMARY KEY,
    expires_at    INTEGER NOT NULL DEFAULT 0,
    added_by      INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vip2_expires ON vip2_users(expires_at);

CREATE TABLE IF NOT EXISTS vip2_cooldowns (
    user_id       INTEGER PRIMARY KEY,
    last_sent_at  INTEGER NOT NULL DEFAULT 0,
    lock_until    INTEGER NOT NULL DEFAULT 0
);
"""


BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")


def next_nine_am_epoch(now_ts: int | None = None) -> int:
    now = datetime.fromtimestamp(
        int(now_ts or time.time()),
        BRAZIL_TZ,
    )
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return int(target.timestamp())


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)

        # Migração leve para bancos já existentes na Square.
        async with self._db.execute(
            "PRAGMA table_info(ffhub_autolike_subscriptions)"
        ) as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        if "notify_chat_id" not in columns:
            await self._db.execute(
                "ALTER TABLE ffhub_autolike_subscriptions "
                "ADD COLUMN notify_chat_id INTEGER"
            )
        if "notify_thread_id" not in columns:
            await self._db.execute(
                "ALTER TABLE ffhub_autolike_subscriptions "
                "ADD COLUMN notify_thread_id INTEGER"
            )
        if "provider" not in columns:
            await self._db.execute(
                "ALTER TABLE ffhub_autolike_subscriptions "
                "ADD COLUMN provider TEXT NOT NULL DEFAULT 'ffhub'"
            )

        # Planos ativos passam a seguir a janela fixa das 09:00.
        now_ts = int(time.time())
        next_9 = next_nine_am_epoch(now_ts)
        await self._db.execute(
            """
            UPDATE ffhub_autolike_subscriptions
            SET next_send_at = ?, updated_at = ?
            WHERE status = 'active' AND next_send_at > ?
            """,
            (next_9, now_ts, now_ts),
        )

        async with self._db.execute("PRAGMA table_info(orders)") as cur:
            order_columns = {str(row["name"]) for row in await cur.fetchall()}
        if "plan_days" not in order_columns:
            await self._db.execute(
                "ALTER TABLE orders ADD COLUMN plan_days INTEGER NOT NULL DEFAULT 0"
            )
        if "metadata" not in order_columns:
            await self._db.execute(
                "ALTER TABLE orders ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
            )
        if "paid_with" not in order_columns:
            await self._db.execute(
                "ALTER TABLE orders ADD COLUMN paid_with TEXT NOT NULL DEFAULT 'pix'"
            )
        if "payment_chat_id" not in order_columns:
            await self._db.execute(
                "ALTER TABLE orders ADD COLUMN payment_chat_id INTEGER"
            )
        if "payment_message_id" not in order_columns:
            await self._db.execute(
                "ALTER TABLE orders ADD COLUMN payment_message_id INTEGER"
            )
        if "purchase_announced" not in order_columns:
            await self._db.execute(
                "ALTER TABLE orders ADD COLUMN purchase_announced INTEGER NOT NULL DEFAULT 0"
            )

        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database não conectado. Chame connect() primeiro.")
        return self._db

    # ----- Users -----
    async def upsert_user(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO users (user_id, username, first_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user_id, username, first_name, int(time.time())),
        )
        await self.db.commit()

    async def get_user(self, user_id: int) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def count_users(self) -> int:
        async with self.db.execute("SELECT COUNT(*) AS c FROM users") as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def all_user_ids(self) -> list[int]:
        async with self.db.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    # ----- VIP -----
    async def set_vip(self, user_id: int, days: int, added_by: int) -> dict[str, Any]:
        """Ativa/renova VIP. days=0 significa permanente."""
        now = int(time.time())
        days = max(0, int(days))
        expires_at = 0 if days == 0 else now + days * 86400

        await self.db.execute(
            """
            INSERT INTO vip_users (user_id, expires_at, added_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                added_by = excluded.added_by,
                updated_at = excluded.updated_at
            """,
            (int(user_id), expires_at, int(added_by), now, now),
        )
        await self.db.commit()
        return {
            "user_id": int(user_id),
            "expires_at": expires_at,
            "added_by": int(added_by),
        }

    async def remove_vip(self, user_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM vip_users WHERE user_id = ?",
            (int(user_id),),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def get_vip(self, user_id: int) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM vip_users WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        data = dict(row)
        expires_at = int(data.get("expires_at") or 0)
        if expires_at and expires_at <= int(time.time()):
            await self.remove_vip(int(user_id))
            return None
        return data

    async def is_vip(self, user_id: int) -> bool:
        return await self.get_vip(user_id) is not None

    async def active_vips(self) -> list[dict[str, Any]]:
        now = int(time.time())
        await self.db.execute(
            "DELETE FROM vip_users WHERE expires_at > 0 AND expires_at <= ?",
            (now,),
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT * FROM vip_users ORDER BY expires_at = 0 DESC, expires_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_active_vips(self) -> int:
        return len(await self.active_vips())

    # ----- VIP2: acesso exclusivo ao /like2 direto -----
    async def set_vip2(self, user_id: int, days: int, added_by: int) -> dict[str, Any]:
        """Ativa/renova VIP2. days=0 significa permanente."""
        now = int(time.time())
        days = max(0, int(days))
        expires_at = 0 if days == 0 else now + days * 86400

        await self.db.execute(
            """
            INSERT INTO vip2_users (user_id, expires_at, added_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                added_by = excluded.added_by,
                updated_at = excluded.updated_at
            """,
            (int(user_id), expires_at, int(added_by), now, now),
        )
        await self.db.commit()
        return {
            "user_id": int(user_id),
            "expires_at": expires_at,
            "added_by": int(added_by),
        }

    async def remove_vip2(self, user_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM vip2_users WHERE user_id = ?",
            (int(user_id),),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def get_vip2(self, user_id: int) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM vip2_users WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        data = dict(row)
        expires_at = int(data.get("expires_at") or 0)
        if expires_at and expires_at <= int(time.time()):
            await self.remove_vip2(int(user_id))
            return None
        return data

    async def is_vip2(self, user_id: int) -> bool:
        return await self.get_vip2(user_id) is not None

    async def active_vip2s(self) -> list[dict[str, Any]]:
        now = int(time.time())
        await self.db.execute(
            "DELETE FROM vip2_users WHERE expires_at > 0 AND expires_at <= ?",
            (now,),
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT * FROM vip2_users ORDER BY expires_at = 0 DESC, expires_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_active_vip2s(self) -> int:
        return len(await self.active_vip2s())

    # ----- VIP2: 1 envio /like2 por usuário a cada 24 horas -----
    async def acquire_vip2_slot(self, user_id: int) -> tuple[bool, int]:
        """Reserva uma tentativa do VIP2 sem consumir as 24h antes do sucesso."""
        now = int(time.time())
        cooldown = 24 * 60 * 60
        lock_seconds = 180

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT last_sent_at, lock_until FROM vip2_cooldowns WHERE user_id = ?",
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()

            last_sent_at = int(row["last_sent_at"]) if row else 0
            lock_until = int(row["lock_until"]) if row else 0

            remaining = (last_sent_at + cooldown) - now
            if remaining > 0:
                await self.db.commit()
                return False, remaining

            if lock_until > now:
                await self.db.commit()
                return False, max(1, lock_until - now)

            await self.db.execute(
                """
                INSERT INTO vip2_cooldowns (user_id, last_sent_at, lock_until)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_sent_at = excluded.last_sent_at,
                    lock_until = excluded.lock_until
                """,
                (int(user_id), last_sent_at, now + lock_seconds),
            )
            await self.db.commit()
            return True, 0
        except Exception:
            await self.db.rollback()
            raise

    async def complete_vip2_slot(self, user_id: int) -> None:
        now = int(time.time())
        await self.db.execute(
            """
            INSERT INTO vip2_cooldowns (user_id, last_sent_at, lock_until)
            VALUES (?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                last_sent_at = excluded.last_sent_at,
                lock_until = 0
            """,
            (int(user_id), now),
        )
        await self.db.commit()

    async def release_vip2_slot(self, user_id: int) -> None:
        await self.db.execute(
            "UPDATE vip2_cooldowns SET lock_until = 0 WHERE user_id = ?",
            (int(user_id),),
        )
        await self.db.commit()

    # ----- Likes: limite de 1 envio por usuário a cada 24 horas -----
    async def acquire_like_slot(self, user_id: int) -> tuple[bool, int]:
        """Reserva um envio evitando corrida entre comandos simultâneos.

        Retorna (permitido, segundos_restantes). A reserva temporária não
        consome as 24h; elas só começam após complete_like_slot().
        """
        now = int(time.time())
        cooldown = 24 * 60 * 60
        lock_seconds = 180

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT last_sent_at, lock_until FROM like_cooldowns WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()

            last_sent_at = int(row["last_sent_at"]) if row else 0
            lock_until = int(row["lock_until"]) if row else 0

            remaining = (last_sent_at + cooldown) - now
            if remaining > 0:
                await self.db.commit()
                return False, remaining

            if lock_until > now:
                await self.db.commit()
                return False, max(1, lock_until - now)

            await self.db.execute(
                """
                INSERT INTO like_cooldowns (user_id, last_sent_at, lock_until)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET lock_until = excluded.lock_until
                """,
                (user_id, last_sent_at, now + lock_seconds),
            )
            await self.db.commit()
            return True, 0
        except Exception:
            await self.db.rollback()
            raise

    async def complete_like_slot(self, user_id: int) -> None:
        now = int(time.time())
        await self.db.execute(
            """
            INSERT INTO like_cooldowns (user_id, last_sent_at, lock_until)
            VALUES (?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                last_sent_at = excluded.last_sent_at,
                lock_until = 0
            """,
            (user_id, now),
        )
        await self.db.commit()

    async def release_like_slot(self, user_id: int) -> None:
        await self.db.execute(
            "UPDATE like_cooldowns SET lock_until = 0 WHERE user_id = ?",
            (user_id,),
        )
        await self.db.commit()

    # ----- Orders -----
    async def create_order(
        self,
        user_id: int,
        product_code: str,
        game_id: str,
        amount: str,
        *,
        plan_days: int = 0,
        metadata: str = "{}",
        paid_with: str = "pix",
    ) -> int:
        now = int(time.time())
        cur = await self.db.execute(
            """
            INSERT INTO orders
                (user_id, product_code, game_id, amount, status,
                 plan_days, metadata, paid_with, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                product_code,
                game_id,
                amount,
                max(0, int(plan_days)),
                metadata or "{}",
                paid_with or "pix",
                now,
                now,
            ),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def set_order_payment(self, order_id: int, payment_id: str) -> None:
        await self.db.execute(
            "UPDATE orders SET payment_id = ?, updated_at = ? WHERE id = ?",
            (payment_id, int(time.time()), order_id),
        )
        await self.db.commit()

    async def set_order_payment_message(
        self,
        order_id: int,
        chat_id: int,
        message_id: int,
    ) -> None:
        await self.db.execute(
            """
            UPDATE orders
            SET payment_chat_id = ?, payment_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(chat_id), int(message_id), int(time.time()), int(order_id)),
        )
        await self.db.commit()

    async def clear_order_payment_message(self, order_id: int) -> None:
        await self.db.execute(
            """
            UPDATE orders
            SET payment_chat_id = NULL,
                payment_message_id = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (int(time.time()), int(order_id)),
        )
        await self.db.commit()

    async def mark_purchase_announced(self, order_id: int) -> bool:
        cur = await self.db.execute(
            """
            UPDATE orders
            SET purchase_announced = 1, updated_at = ?
            WHERE id = ? AND purchase_announced = 0
            """,
            (int(time.time()), int(order_id)),
        )
        await self.db.commit()
        return bool(cur.rowcount)

    async def update_order_status(
        self, order_id: int, status: str, delivery_info: str | None = None
    ) -> None:
        if delivery_info is not None:
            await self.db.execute(
                "UPDATE orders SET status = ?, delivery_info = ?, updated_at = ? WHERE id = ?",
                (status, delivery_info, int(time.time()), order_id),
            )
        else:
            await self.db.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                (status, int(time.time()), order_id),
            )
        await self.db.commit()

    async def get_order(self, order_id: int) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def get_order_by_payment(self, payment_id: str) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders WHERE payment_id = ?", (payment_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def set_order_game_id(self, order_id: int, game_id: str) -> None:
        await self.db.execute(
            "UPDATE orders SET game_id = ?, updated_at = ? WHERE id = ?",
            (game_id, int(time.time()), order_id),
        )
        await self.db.commit()

    async def get_awaiting_id_order(self, user_id: int) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders "
            "WHERE user_id = ? AND status = 'awaiting_id' "
            "ORDER BY id ASC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def count_orders(self, status: str | None = None) -> int:
        if status:
            query = "SELECT COUNT(*) AS c FROM orders WHERE status = ?"
            params: tuple = (status,)
        else:
            query = "SELECT COUNT(*) AS c FROM orders"
            params = ()
        async with self.db.execute(query, params) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def recent_orders(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def pending_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders "
            "WHERE status = 'pending' AND payment_id IS NOT NULL "
            "ORDER BY id ASC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ----- Consultas do suporte (IA) -----
    async def orders_by_user(self, user_id: int, limit: int = 15) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (int(user_id), int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def orders_by_game_id(self, game_id: str, limit: int = 15) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders WHERE game_id = ? ORDER BY id DESC LIMIT ?",
            (str(game_id), int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def failed_orders(self, limit: int = 15) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM orders WHERE status = 'failed' ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def autolike_subscriptions_for(
        self,
        *,
        user_id: int | None = None,
        game_id: str | None = None,
        order_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        where, params = [], []
        if user_id is not None:
            where.append("user_id = ?")
            params.append(int(user_id))
        if game_id is not None:
            where.append("game_id = ?")
            params.append(str(game_id))
        if order_id is not None:
            where.append("order_id = ?")
            params.append(int(order_id))
        if not where:
            return []
        params.append(int(limit))
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions WHERE "
            + " AND ".join(where)
            + " ORDER BY id DESC LIMIT ?",
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def autolike_history(
        self, subscription_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_history WHERE subscription_id = ? "
            "ORDER BY sent_at DESC, id DESC LIMIT ?",
            (int(subscription_id), int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def failed_autolike_subscriptions(self, limit: int = 15) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions "
            "WHERE last_result IS NOT NULL AND last_result != '' "
            "AND (last_result LIKE '%erro%' OR last_result LIKE '%error%' "
            "OR last_result LIKE '%fail%' OR last_result LIKE '%falha%') "
            "ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def wallet_transactions(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM wallet_transactions WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(user_id), int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ----- Entradas pendentes do cliente -----
    async def set_customer_pending_input(
        self,
        user_id: int,
        action: str,
        value: str = "",
        ttl_seconds: int = 600,
    ) -> None:
        now = int(time.time())
        await self.db.execute(
            """
            INSERT INTO customer_pending_inputs
                (user_id, action, value, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                action = excluded.action,
                value = excluded.value,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (
                int(user_id),
                str(action),
                str(value or ""),
                now + max(60, int(ttl_seconds)),
                now,
            ),
        )
        await self.db.commit()

    async def get_customer_pending_input(
        self,
        user_id: int,
    ) -> Optional[dict[str, Any]]:
        now = int(time.time())
        async with self.db.execute(
            """
            SELECT * FROM customer_pending_inputs
            WHERE user_id = ? AND expires_at > ?
            """,
            (int(user_id), now),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return dict(row)

        await self.db.execute(
            "DELETE FROM customer_pending_inputs WHERE user_id = ?",
            (int(user_id),),
        )
        await self.db.commit()
        return None

    async def clear_customer_pending_input(self, user_id: int) -> None:
        await self.db.execute(
            "DELETE FROM customer_pending_inputs WHERE user_id = ?",
            (int(user_id),),
        )
        await self.db.commit()

    # ----- Saldo / carteira -----
    async def get_balance_cents(self, user_id: int) -> int:
        async with self.db.execute(
            "SELECT balance_cents FROM wallets WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
        return int(row["balance_cents"]) if row else 0

    async def credit_wallet(
        self,
        user_id: int,
        amount_cents: int,
        *,
        order_id: int | None = None,
        description: str = "",
    ) -> int:
        amount_cents = max(0, int(amount_cents))
        now = int(time.time())
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            if order_id is not None:
                async with self.db.execute(
                    "SELECT 1 FROM wallet_transactions "
                    "WHERE order_id = ? AND kind = 'credit' LIMIT 1",
                    (int(order_id),),
                ) as cur:
                    already = await cur.fetchone()
                if already:
                    await self.db.commit()
                    return await self.get_balance_cents(user_id)

            await self.db.execute(
                """
                INSERT INTO wallets (user_id, balance_cents, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance_cents = wallets.balance_cents + excluded.balance_cents,
                    updated_at = excluded.updated_at
                """,
                (int(user_id), amount_cents, now),
            )
            await self.db.execute(
                """
                INSERT INTO wallet_transactions
                    (user_id, amount_cents, kind, order_id, description, created_at)
                VALUES (?, ?, 'credit', ?, ?, ?)
                """,
                (int(user_id), amount_cents, order_id, description, now),
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return await self.get_balance_cents(user_id)

    async def spend_wallet(
        self,
        user_id: int,
        amount_cents: int,
        *,
        order_id: int | None = None,
        description: str = "",
    ) -> tuple[bool, int]:
        amount_cents = max(0, int(amount_cents))
        now = int(time.time())
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT balance_cents FROM wallets WHERE user_id = ?",
                (int(user_id),),
            ) as cur:
                row = await cur.fetchone()
            balance = int(row["balance_cents"]) if row else 0
            if balance < amount_cents:
                await self.db.commit()
                return False, balance

            new_balance = balance - amount_cents
            await self.db.execute(
                """
                INSERT INTO wallets (user_id, balance_cents, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance_cents = excluded.balance_cents,
                    updated_at = excluded.updated_at
                """,
                (int(user_id), new_balance, now),
            )
            await self.db.execute(
                """
                INSERT INTO wallet_transactions
                    (user_id, amount_cents, kind, order_id, description, created_at)
                VALUES (?, ?, 'debit', ?, ?, ?)
                """,
                (int(user_id), -amount_cents, order_id, description, now),
            )
            await self.db.commit()
            return True, new_balance
        except Exception:
            await self.db.rollback()
            raise

    # ----- Revendedores -----
    async def is_reseller(self, user_id: int) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM resellers WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
        return row is not None

    async def activate_reseller(self, user_id: int, order_id: int | None = None) -> None:
        now = int(time.time())
        await self.db.execute(
            """
            INSERT INTO resellers (user_id, order_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                order_id = COALESCE(excluded.order_id, resellers.order_id),
                updated_at = excluded.updated_at
            """,
            (int(user_id), order_id, now, now),
        )
        await self.db.commit()

    async def remove_reseller(self, user_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM resellers WHERE user_id = ?",
            (int(user_id),),
        )
        await self.db.commit()
        return bool(cur.rowcount)

    async def reseller_count(self) -> int:
        async with self.db.execute("SELECT COUNT(*) AS c FROM resellers") as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row else 0

    # ----- Aluguel do bot -----
    async def create_bot_rental(
        self,
        order_id: int,
        user_id: int,
        days: int = 30,
    ) -> int:
        now = int(time.time())
        expires = now + max(1, int(days)) * 86400
        cur = await self.db.execute(
            """
            INSERT OR IGNORE INTO bot_rentals
                (order_id, user_id, status, starts_at, expires_at, created_at, updated_at)
            VALUES (?, ?, 'awaiting_group', ?, ?, ?, ?)
            """,
            (int(order_id), int(user_id), now, expires, now, now),
        )
        await self.db.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        async with self.db.execute(
            "SELECT id FROM bot_rentals WHERE order_id = ?",
            (int(order_id),),
        ) as q:
            row = await q.fetchone()
        return int(row["id"]) if row else 0

    async def get_bot_rental(self, rental_id: int) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM bot_rentals WHERE id = ?",
            (int(rental_id),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def bind_bot_rental(
        self,
        rental_id: int,
        user_id: int,
        chat_id: int,
    ) -> bool:
        now = int(time.time())
        cur = await self.db.execute(
            """
            UPDATE bot_rentals
            SET chat_id = ?, status = 'active', updated_at = ?
            WHERE id = ? AND user_id = ?
              AND status IN ('awaiting_group', 'active')
              AND expires_at > ?
            """,
            (int(chat_id), now, int(rental_id), int(user_id), now),
        )
        await self.db.commit()
        return bool(cur.rowcount)

    async def active_rental_for_chat(self, chat_id: int) -> Optional[dict[str, Any]]:
        now = int(time.time())
        async with self.db.execute(
            """
            SELECT * FROM bot_rentals
            WHERE chat_id = ? AND status = 'active' AND expires_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (int(chat_id), now),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    # ----- Auto-Like Premium FFHub -----
    async def create_ffhub_autolike_subscription(
        self,
        order_id: int,
        user_id: int,
        game_id: str,
        days_total: int,
    ) -> int:
        now = int(time.time())
        cur = await self.db.execute(
            """
            INSERT OR IGNORE INTO ffhub_autolike_subscriptions
                (order_id, user_id, game_id, days_total, sends_done,
                 next_send_at, status, provider, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, 'active', 'ffhub', ?, ?)
            """,
            (
                order_id,
                user_id,
                game_id,
                days_total,
                next_nine_am_epoch(now),
                now,
                now,
            ),
        )
        await self.db.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        async with self.db.execute(
            "SELECT id FROM ffhub_autolike_subscriptions WHERE order_id = ?",
            (order_id,),
        ) as existing:
            row = await existing.fetchone()
        return int(row["id"]) if row else 0

    async def create_owner_ffhub_autolike_subscription(
        self,
        user_id: int,
        game_id: str,
        days_total: int,
        notify_chat_id: int | None = None,
        notify_thread_id: int | None = None,
    ) -> tuple[int, bool]:
        """Cria Auto-Like FFHub manual do dono sem precisar de pedido/PIX.

        Retorna (subscription_id, created). Se já existir uma assinatura ativa
        para o mesmo UID, não duplica os envios.
        """
        now = int(time.time())
        days_total = max(1, int(days_total))

        async with self.db.execute(
            "SELECT id FROM ffhub_autolike_subscriptions "
            "WHERE game_id = ? AND status = 'active' AND provider = 'ffhub' "
            "ORDER BY id DESC LIMIT 1",
            (str(game_id),),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            existing_id = int(existing["id"])
            if notify_chat_id is not None:
                await self.db.execute(
                    "UPDATE ffhub_autolike_subscriptions "
                    "SET notify_chat_id = ?, notify_thread_id = ?, updated_at = ? "
                    "WHERE id = ?",
                    (
                        int(notify_chat_id),
                        int(notify_thread_id) if notify_thread_id is not None else None,
                        now,
                        existing_id,
                    ),
                )
                await self.db.commit()
            return existing_id, False

        async with self.db.execute(
            "SELECT MIN(order_id) AS min_id FROM ffhub_autolike_subscriptions "
            "WHERE order_id < 0"
        ) as cur:
            row = await cur.fetchone()
        min_id = int(row["min_id"]) if row and row["min_id"] is not None else 0
        synthetic_order_id = min_id - 1 if min_id < 0 else -1

        cur = await self.db.execute(
            """
            INSERT INTO ffhub_autolike_subscriptions
                (order_id, user_id, game_id, days_total, sends_done,
                 next_send_at, status, notify_chat_id, notify_thread_id,
                 provider, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, 'active', ?, ?, 'ffhub', ?, ?)
            """,
            (
                synthetic_order_id,
                int(user_id),
                str(game_id),
                days_total,
                next_nine_am_epoch(now),
                int(notify_chat_id) if notify_chat_id is not None else None,
                int(notify_thread_id) if notify_thread_id is not None else None,
                now,
                now,
            ),
        )
        await self.db.commit()
        return int(cur.lastrowid), True

    async def active_ffhub_autolikes(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions "
            "WHERE status = 'active' AND provider = 'ffhub' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def cancel_ffhub_autolike_by_uid(self, game_id: str) -> int:
        cur = await self.db.execute(
            "UPDATE ffhub_autolike_subscriptions "
            "SET status = 'cancelled', updated_at = ? "
            "WHERE game_id = ? AND status = 'active' AND provider = 'ffhub'",
            (int(time.time()), str(game_id)),
        )
        await self.db.commit()
        return int(cur.rowcount or 0)

    async def get_ffhub_autolike_status(
        self, game_id: str
    ) -> Optional[dict[str, Any]]:
        """Retorna a assinatura mais recente do UID com totais e última entrega."""
        async with self.db.execute(
            """
            SELECT
                s.*,
                COALESCE((
                    SELECT SUM(h.likes_sent)
                    FROM ffhub_autolike_history h
                    WHERE h.subscription_id = s.id
                ), 0) AS total_likes_sent,
                (
                    SELECT h.nickname
                    FROM ffhub_autolike_history h
                    WHERE h.subscription_id = s.id
                      AND h.nickname IS NOT NULL
                      AND h.nickname != ''
                    ORDER BY h.sent_at DESC, h.id DESC
                    LIMIT 1
                ) AS nickname,
                (
                    SELECT h.likes_sent
                    FROM ffhub_autolike_history h
                    WHERE h.subscription_id = s.id
                    ORDER BY h.sent_at DESC, h.id DESC
                    LIMIT 1
                ) AS last_likes_sent,
                (
                    SELECT h.likes_before
                    FROM ffhub_autolike_history h
                    WHERE h.subscription_id = s.id
                    ORDER BY h.sent_at DESC, h.id DESC
                    LIMIT 1
                ) AS last_likes_before,
                (
                    SELECT h.likes_after
                    FROM ffhub_autolike_history h
                    WHERE h.subscription_id = s.id
                    ORDER BY h.sent_at DESC, h.id DESC
                    LIMIT 1
                ) AS last_likes_after
            FROM ffhub_autolike_subscriptions s
            WHERE s.game_id = ? AND s.provider = 'ffhub'
            ORDER BY (s.status = 'active') DESC, s.id DESC
            LIMIT 1
            """,
            (str(game_id),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def active_ffhub_autolikes_with_stats(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with self.db.execute(
            """
            SELECT
                s.*,
                COALESCE(SUM(h.likes_sent), 0) AS total_likes_sent,
                (
                    SELECT h2.nickname
                    FROM ffhub_autolike_history h2
                    WHERE h2.subscription_id = s.id
                      AND h2.nickname IS NOT NULL
                      AND h2.nickname != ''
                    ORDER BY h2.sent_at DESC, h2.id DESC
                    LIMIT 1
                ) AS nickname,
                (
                    SELECT h3.likes_sent
                    FROM ffhub_autolike_history h3
                    WHERE h3.subscription_id = s.id
                    ORDER BY h3.sent_at DESC, h3.id DESC
                    LIMIT 1
                ) AS last_likes_sent
            FROM ffhub_autolike_subscriptions s
            LEFT JOIN ffhub_autolike_history h
              ON h.subscription_id = s.id
            WHERE s.status = 'active' AND s.provider = 'ffhub'
            GROUP BY s.id
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def due_ffhub_autolikes(self, limit: int = 50) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions "
            "WHERE status = 'active' AND provider = 'ffhub' "
            "AND next_send_at <= ? "
            "ORDER BY next_send_at ASC LIMIT ?",
            (now, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def reserve_ffhub_autolike_attempt(self, subscription_id: int) -> None:
        now = int(time.time())
        await self.db.execute(
            "UPDATE ffhub_autolike_subscriptions "
            "SET next_send_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'active'",
            (next_nine_am_epoch(now), now, subscription_id),
        )
        await self.db.commit()

    async def complete_ffhub_autolike_send(
        self,
        subscription_id: int,
        result_text: str = "",
        *,
        nickname: str | None = None,
        likes_before: int | None = None,
        likes_after: int | None = None,
        likes_sent: int | None = None,
    ) -> dict[str, Any] | None:
        now = int(time.time())
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions WHERE id = ?",
            (subscription_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        sends_done = int(row["sends_done"]) + 1
        days_total = int(row["days_total"])
        status = "completed" if sends_done >= days_total else "active"

        next_send_at = (
            0
            if status == "completed"
            else next_nine_am_epoch(now)
        )

        sent_value = max(0, int(likes_sent or 0))
        before_value = int(likes_before) if likes_before is not None else None
        after_value = int(likes_after) if likes_after is not None else None

        await self.db.execute(
            "UPDATE ffhub_autolike_subscriptions "
            "SET sends_done = ?, last_sent_at = ?, next_send_at = ?, "
            "status = ?, last_result = ?, updated_at = ? WHERE id = ?",
            (
                sends_done,
                now,
                next_send_at,
                status,
                result_text,
                now,
                subscription_id,
            ),
        )

        await self.db.execute(
            """
            INSERT INTO ffhub_autolike_history
                (subscription_id, game_id, nickname, likes_before,
                 likes_after, likes_sent, raw_result, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subscription_id,
                str(row["game_id"]),
                nickname,
                before_value,
                after_value,
                sent_value,
                result_text,
                now,
            ),
        )
        await self.db.commit()

        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions WHERE id = ?",
            (subscription_id,),
        ) as cur:
            updated = await cur.fetchone()
        return dict(updated) if updated else None

    async def retry_ffhub_autolike_later(
        self, subscription_id: int, result_text: str = "", delay_seconds: int = 900
    ) -> None:
        # Mesmo em falha, não foge da janela fixa: tenta novamente às 09:00
        # do próximo dia e mantém a entrega pendente no total do plano.
        now = int(time.time())
        await self.db.execute(
            "UPDATE ffhub_autolike_subscriptions "
            "SET next_send_at = ?, last_result = ?, updated_at = ? "
            "WHERE id = ? AND status = 'active'",
            (
                next_nine_am_epoch(now),
                result_text,
                now,
                subscription_id,
            ),
        )
        await self.db.commit()

    async def create_autosystem_autolike_subscription(
        self,
        order_id: int,
        user_id: int,
        game_id: str,
        days_total: int,
    ) -> int:
        now = int(time.time())
        cur = await self.db.execute(
            """
            INSERT OR IGNORE INTO ffhub_autolike_subscriptions
                (order_id, user_id, game_id, days_total, sends_done,
                 next_send_at, status, provider, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, 'active', 'autosystem', ?, ?)
            """,
            (
                int(order_id),
                int(user_id),
                str(game_id),
                max(1, int(days_total)),
                next_nine_am_epoch(now),
                now,
                now,
            ),
        )
        await self.db.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        async with self.db.execute(
            "SELECT id FROM ffhub_autolike_subscriptions WHERE order_id = ?",
            (int(order_id),),
        ) as existing:
            row = await existing.fetchone()
        return int(row["id"]) if row else 0

    async def due_autosystem_autolikes(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self.db.execute(
            """
            SELECT * FROM ffhub_autolike_subscriptions
            WHERE status = 'active'
              AND provider = 'autosystem'
              AND next_send_at <= ?
            ORDER BY next_send_at ASC
            LIMIT ?
            """,
            (now, int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ----- Gift cards -----
    async def create_gift_card(
        self,
        code: str,
        amount_cents: int,
        created_by: int,
        max_uses: int = 1,
        expires_at: int = 0,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO gift_cards
                (code, amount_cents, max_uses, uses_count, created_by,
                 expires_at, is_active, created_at)
            VALUES (?, ?, ?, 0, ?, ?, 1, ?)
            """,
            (
                str(code).upper(),
                max(1, int(amount_cents)),
                max(1, int(max_uses)),
                int(created_by),
                max(0, int(expires_at)),
                int(time.time()),
            ),
        )
        await self.db.commit()

    async def get_gift_card(self, code: str) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM gift_cards WHERE code = ?",
            (str(code).strip().upper(),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def revoke_gift_card(self, code: str) -> bool:
        cur = await self.db.execute(
            "UPDATE gift_cards SET is_active = 0 WHERE code = ?",
            (str(code).strip().upper(),),
        )
        await self.db.commit()
        return bool(cur.rowcount)

    async def redeem_gift_card(
        self,
        code: str,
        user_id: int,
    ) -> tuple[bool, str, int, int]:
        code = str(code).strip().upper()
        user_id = int(user_id)
        now = int(time.time())

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT * FROM gift_cards WHERE code = ?",
                (code,),
            ) as cur:
                card = await cur.fetchone()

            if not card:
                await self.db.rollback()
                return False, "Gift Card não encontrado.", 0, 0

            data = dict(card)
            if not int(data.get("is_active") or 0):
                await self.db.rollback()
                return False, "Este Gift Card está desativado.", 0, 0
            if int(data.get("expires_at") or 0) > 0 and int(data["expires_at"]) <= now:
                await self.db.rollback()
                return False, "Este Gift Card expirou.", 0, 0
            if int(data.get("uses_count") or 0) >= int(data.get("max_uses") or 1):
                await self.db.rollback()
                return False, "Este Gift Card já foi totalmente utilizado.", 0, 0

            async with self.db.execute(
                "SELECT 1 FROM gift_card_redemptions "
                "WHERE code = ? AND user_id = ? LIMIT 1",
                (code, user_id),
            ) as cur:
                already = await cur.fetchone()
            if already:
                await self.db.rollback()
                return False, "Você já resgatou este Gift Card.", 0, 0

            amount_cents = int(data["amount_cents"])

            async with self.db.execute(
                "SELECT balance_cents FROM wallets WHERE user_id = ?",
                (user_id,),
            ) as cur:
                wallet = await cur.fetchone()
            current = int(wallet["balance_cents"]) if wallet else 0
            new_balance = current + amount_cents

            await self.db.execute(
                """
                INSERT INTO wallets (user_id, balance_cents, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance_cents = excluded.balance_cents,
                    updated_at = excluded.updated_at
                """,
                (user_id, new_balance, now),
            )
            await self.db.execute(
                """
                INSERT INTO wallet_transactions
                    (user_id, amount_cents, kind, order_id, description, created_at)
                VALUES (?, ?, 'gift_card', NULL, ?, ?)
                """,
                (user_id, amount_cents, f"Gift Card {code}", now),
            )
            await self.db.execute(
                """
                INSERT INTO gift_card_redemptions
                    (code, user_id, amount_cents, redeemed_at)
                VALUES (?, ?, ?, ?)
                """,
                (code, user_id, amount_cents, now),
            )
            await self.db.execute(
                "UPDATE gift_cards SET uses_count = uses_count + 1 WHERE code = ?",
                (code,),
            )
            await self.db.commit()
            return True, "Gift Card resgatado.", amount_cents, new_balance
        except Exception:
            await self.db.rollback()
            raise

    # ----- Configurações privadas do dono -----
    async def get_owner_setting(self, key: str) -> str | None:
        async with self.db.execute(
            "SELECT value FROM owner_settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return str(row["value"]) if row else None

    async def set_owner_setting(self, key: str, value: str) -> None:
        await self.db.execute(
            """
            INSERT INTO owner_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, int(time.time())),
        )
        await self.db.commit()

    async def all_owner_settings(self) -> dict[str, str]:
        async with self.db.execute(
            "SELECT key, value FROM owner_settings"
        ) as cur:
            rows = await cur.fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows}

    # ----- Product settings (preço / estoque geridos pelo admin) -----
    async def init_product_settings(self, products: dict) -> None:
        """Garante uma linha por produto do catálogo (não sobrescreve valores existentes)."""
        for code, product in products.items():
            await self.db.execute(
                "INSERT OR IGNORE INTO product_settings (code, price, stock) VALUES (?, ?, ?)",
                (code, str(product.price), -1),
            )
        await self.db.commit()

    async def get_product_setting(self, code: str) -> Optional[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM product_settings WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def all_product_settings(self) -> dict[str, dict[str, Any]]:
        async with self.db.execute("SELECT * FROM product_settings") as cur:
            rows = await cur.fetchall()
        return {r["code"]: dict(r) for r in rows}

    async def set_price(self, code: str, price: str) -> None:
        await self.db.execute(
            """
            INSERT INTO product_settings (code, price, stock)
            VALUES (?, ?, -1)
            ON CONFLICT(code) DO UPDATE SET price = excluded.price
            """,
            (code, price),
        )
        await self.db.commit()

    async def set_stock(self, code: str, stock: int) -> None:
        await self.db.execute(
            """
            INSERT INTO product_settings (code, price, stock)
            VALUES (?, NULL, ?)
            ON CONFLICT(code) DO UPDATE SET stock = excluded.stock
            """,
            (code, stock),
        )
        await self.db.commit()

    async def decrement_stock(self, code: str) -> None:
        """Baixa 1 do estoque, exceto quando ilimitado (-1). Não deixa negativo."""
        await self.db.execute(
            "UPDATE product_settings SET stock = stock - 1 "
            "WHERE code = ? AND stock > 0",
            (code,),
        )
        await self.db.commit()
