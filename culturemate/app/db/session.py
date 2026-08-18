"""psycopg3 비동기 커넥션 풀. pgvector 타입 등록 포함."""
from __future__ import annotations

from contextlib import asynccontextmanager

from app.config import get_settings

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        from pgvector.psycopg import register_vector_async
        from psycopg_pool import AsyncConnectionPool

        async def _configure(conn):
            await register_vector_async(conn)

        _pool = AsyncConnectionPool(
            conninfo=get_settings().pg_dsn,
            min_size=1,
            max_size=10,
            configure=_configure,
            open=False,
        )
        await _pool.open()
    return _pool


@asynccontextmanager
async def acquire():
    pool = await get_pool()
    async with pool.connection() as conn:
        yield conn


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
