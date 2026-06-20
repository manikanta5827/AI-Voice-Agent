import os
from datetime import datetime, timezone

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"), min_size=1, max_size=5)
    return _pool


async def insert_call(call_sid: str, stream_sid: str):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO calls (call_sid, stream_sid, started_at) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        call_sid, stream_sid, datetime.now(timezone.utc),
    )


async def insert_message(call_sid: str, role: str, content: str):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO messages (call_sid, role, content, created_at) VALUES ($1, $2, $3, $4)",
        call_sid, role, content, datetime.now(timezone.utc),
    )


async def end_call(call_sid: str):
    pool = await get_pool()
    await pool.execute(
        "UPDATE calls SET ended_at = $1 WHERE call_sid = $2",
        datetime.now(timezone.utc), call_sid,
    )
