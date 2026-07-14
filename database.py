from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def database_enabled() -> bool:
    return bool(DATABASE_URL)


async def _connect() -> psycopg.AsyncConnection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")

    return await psycopg.AsyncConnection.connect(
        DATABASE_URL,
        row_factory=dict_row,
    )


async def init_database(max_attempts: int = 20) -> bool:
    """Create tables and indexes. Keep the API alive if storage is unavailable."""
    if not database_enabled():
        print("WARNING: DATABASE_URL is not configured. Analytics storage is disabled.")
        return False

    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            language_code TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            app_open_count BIGINT NOT NULL DEFAULT 0,
            act_count BIGINT NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS events (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS acts (
            id BIGSERIAL PRIMARY KEY,
            act_number TEXT NOT NULL UNIQUE,
            telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
            sto TEXT,
            master TEXT,
            master_phone TEXT,
            car TEXT,
            comment TEXT,
            items_count INTEGER NOT NULL,
            items JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE acts
        ADD COLUMN IF NOT EXISTS master_phone TEXT
        """,
        """
        ALTER TABLE acts
        ADD COLUMN IF NOT EXISTS comment TEXT
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_events_type_created_at
        ON events (event_type, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_events_telegram_created_at
        ON events (telegram_id, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_acts_created_at
        ON acts (created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_acts_telegram_created_at
        ON acts (telegram_id, created_at DESC)
        """,
    ]

    for attempt in range(1, max_attempts + 1):
        try:
            async with await _connect() as conn:
                for statement in statements:
                    await conn.execute(statement)

            print("PostgreSQL schema is ready.")
            return True
        except Exception as error:
            print(
                f"WARNING: PostgreSQL init attempt {attempt}/{max_attempts} failed: {error}"
            )
            if attempt < max_attempts:
                await asyncio.sleep(min(3 * attempt, 10))

    print("WARNING: PostgreSQL storage remains unavailable. Core PDF flow stays enabled.")
    return False


async def _upsert_user(conn: psycopg.AsyncConnection, user: dict[str, Any]) -> int:
    telegram_id = int(user["id"])

    await conn.execute(
        """
        INSERT INTO users (
            telegram_id,
            first_name,
            last_name,
            username,
            language_code
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            username = EXCLUDED.username,
            language_code = EXCLUDED.language_code,
            last_seen_at = NOW()
        """,
        (
            telegram_id,
            user.get("first_name"),
            user.get("last_name"),
            user.get("username"),
            user.get("language_code"),
        ),
    )

    return telegram_id


async def touch_user(user: dict[str, Any]) -> bool:
    if not database_enabled():
        return False

    async with await _connect() as conn:
        await _upsert_user(conn, user)

    return True


async def track_app_open(user: dict[str, Any]) -> bool:
    if not database_enabled():
        return False

    async with await _connect() as conn:
        telegram_id = await _upsert_user(conn, user)

        await conn.execute(
            """
            UPDATE users
            SET app_open_count = app_open_count + 1,
                last_seen_at = NOW()
            WHERE telegram_id = %s
            """,
            (telegram_id,),
        )

        await conn.execute(
            """
            INSERT INTO events (telegram_id, event_type)
            VALUES (%s, 'app_open')
            """,
            (telegram_id,),
        )

    return True


async def record_sent_act(
    *,
    user: dict[str, Any],
    act_number: str,
    sto: str,
    master: str,
    master_phone: str,
    car: str,
    comment: str,
    items: list[dict[str, Any]],
) -> bool:
    if not database_enabled():
        return False

    async with await _connect() as conn:
        telegram_id = await _upsert_user(conn, user)

        await conn.execute(
            """
            INSERT INTO acts (
                act_number,
                telegram_id,
                sto,
                master,
                master_phone,
                car,
                comment,
                items_count,
                items
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (act_number) DO NOTHING
            """,
            (
                act_number,
                telegram_id,
                sto or None,
                master or None,
                master_phone or None,
                car or None,
                comment or None,
                len(items),
                Jsonb(items),
            ),
        )

        await conn.execute(
            """
            UPDATE users
            SET act_count = act_count + 1,
                last_seen_at = NOW()
            WHERE telegram_id = %s
            """,
            (telegram_id,),
        )

        await conn.execute(
            """
            INSERT INTO events (
                telegram_id,
                event_type,
                metadata
            )
            VALUES (%s, 'act_sent', %s)
            """,
            (
                telegram_id,
                Jsonb(
                    {
                        "act_number": act_number,
                        "items_count": len(items),
                    }
                ),
            ),
        )

    return True


async def get_stats(today_start: datetime) -> dict[str, int]:
    if not database_enabled():
        raise RuntimeError("Database storage is disabled.")

    async with await _connect() as conn:
        users_row = await (
            await conn.execute("SELECT COUNT(*) AS count FROM users")
        ).fetchone()

        opens_row = await (
            await conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM events
                WHERE event_type = 'app_open'
                """
            )
        ).fetchone()

        acts_row = await (
            await conn.execute("SELECT COUNT(*) AS count FROM acts")
        ).fetchone()

        database_size_row = await (
            await conn.execute(
                """
                SELECT pg_database_size(current_database()) AS bytes
                """
            )
        ).fetchone()

        today_row = await (
            await conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE event_type = 'app_open'
                    ) AS app_opens,
                    COUNT(*) FILTER (
                        WHERE event_type = 'act_sent'
                    ) AS acts,
                    COUNT(DISTINCT telegram_id) AS active_users
                FROM events
                WHERE created_at >= %s
                """,
                (today_start,),
            )
        ).fetchone()

    return {
        "users": int(users_row["count"]),
        "app_opens": int(opens_row["count"]),
        "acts": int(acts_row["count"]),
        "database_size_bytes": int(database_size_row["bytes"] or 0),
        "today_app_opens": int(today_row["app_opens"] or 0),
        "today_acts": int(today_row["acts"] or 0),
        "today_active_users": int(today_row["active_users"] or 0),
    }


async def get_acts_count() -> int:
    if not database_enabled():
        raise RuntimeError("Database storage is disabled.")

    async with await _connect() as conn:
        row = await (
            await conn.execute("SELECT COUNT(*) AS count FROM acts")
        ).fetchone()

    return int(row["count"])


async def get_acts_page(
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not database_enabled():
        raise RuntimeError("Database storage is disabled.")

    async with await _connect() as conn:
        cursor = await conn.execute(
            """
            SELECT
                a.act_number,
                a.sto,
                a.master,
                a.master_phone,
                a.car,
                a.comment,
                a.items_count,
                a.created_at,
                u.telegram_id,
                u.first_name,
                u.last_name,
                u.username
            FROM acts a
            LEFT JOIN users u
                ON u.telegram_id = a.telegram_id
            ORDER BY a.created_at DESC
            LIMIT %s
            OFFSET %s
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()

    return list(rows)


async def get_all_acts_for_export() -> list[dict[str, Any]]:
    if not database_enabled():
        raise RuntimeError("Database storage is disabled.")

    async with await _connect() as conn:
        cursor = await conn.execute(
            """
            SELECT
                a.act_number,
                a.sto,
                a.master,
                a.master_phone,
                a.car,
                a.comment,
                a.items_count,
                a.items,
                a.created_at,
                u.telegram_id,
                u.first_name,
                u.last_name,
                u.username
            FROM acts a
            LEFT JOIN users u
                ON u.telegram_id = a.telegram_id
            ORDER BY a.created_at DESC
            """
        )
        rows = await cursor.fetchall()

    return list(rows)


async def get_top_users(limit: int = 10) -> list[dict[str, Any]]:
    if not database_enabled():
        raise RuntimeError("Database storage is disabled.")

    async with await _connect() as conn:
        cursor = await conn.execute(
            """
            SELECT
                telegram_id,
                first_name,
                last_name,
                username,
                app_open_count,
                act_count,
                first_seen_at,
                last_seen_at
            FROM users
            ORDER BY
                act_count DESC,
                app_open_count DESC,
                last_seen_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

    return list(rows)
