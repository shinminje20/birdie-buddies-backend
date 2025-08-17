import logging, time
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text, event
from contextlib import asynccontextmanager
from .config import get_settings

log = logging.getLogger("app.sql")
S = get_settings()

_settings = get_settings()

engine = create_async_engine(
    _settings.DATABASE_URL,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def lifespan_db():
    # Place for startup checks if needed
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        yield
    finally:
        await engine.dispose()


async def db_health() -> bool:
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.perf_counter()

@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed_ms = int((time.perf_counter() - getattr(context, "_query_start_time", time.perf_counter())) * 1000)
    if elapsed_ms >= S.SLOW_QUERY_MS:
        log.warning("slow_query", extra={"elapsed_ms": elapsed_ms, "sql": statement[:200]})