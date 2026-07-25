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
