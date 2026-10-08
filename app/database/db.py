"""Acesso ao banco de dados (SQLite assíncrono)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

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
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ffhub_autolike_due
ON ffhub_autolike_subscriptions(status, next_send_at);

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
"""


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
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
        self, user_id: int, product_code: str, game_id: str, amount: str
    ) -> int:
        now = int(time.time())
        cur = await self.db.execute(
            """
            INSERT INTO orders
                (user_id, product_code, game_id, amount, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (user_id, product_code, game_id, amount, now, now),
        )
        await self.db.commit()
        return cur.lastrowid

    async def set_order_payment(self, order_id: int, payment_id: str) -> None:
        await self.db.execute(
            "UPDATE orders SET payment_id = ?, updated_at = ? WHERE id = ?",
            (payment_id, int(time.time()), order_id),
        )
        await self.db.commit()

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
                 next_send_at, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, 'active', ?, ?)
            """,
            (order_id, user_id, game_id, days_total, now, now, now),
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

    async def due_ffhub_autolikes(self, limit: int = 50) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self.db.execute(
            "SELECT * FROM ffhub_autolike_subscriptions "
            "WHERE status = 'active' AND next_send_at <= ? "
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
            (now + 86400, now, subscription_id),
        )
        await self.db.commit()

    async def complete_ffhub_autolike_send(
        self, subscription_id: int, result_text: str = ""
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
        next_send_at = 0 if status == "completed" else now + 86400

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
        now = int(time.time())
        await self.db.execute(
            "UPDATE ffhub_autolike_subscriptions "
            "SET next_send_at = ?, last_result = ?, updated_at = ? "
            "WHERE id = ? AND status = 'active'",
            (now + max(60, delay_seconds), result_text, now, subscription_id),
        )
        await self.db.commit()

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
            "UPDATE product_settings SET price = ? WHERE code = ?", (price, code)
        )
        await self.db.commit()

    async def set_stock(self, code: str, stock: int) -> None:
        await self.db.execute(
            "UPDATE product_settings SET stock = ? WHERE code = ?", (stock, code)
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
