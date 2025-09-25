import uuid
import pytest_asyncio
from datetime import datetime, timezone

# IMPORTANT: import engine/SessionLocal only after config is loaded
from app.db import engine, SessionLocal
from app.repos import users as users_repo
from backend.app.repos import ledger_repo as ledger_repo
from backend.app.repos import session_repo as sess_repo
from redis.asyncio import from_url
from app.config import get_settings

# Clean DB before each test, on the SAME loop as the test function.
# Also DISPOSE the engine after each test so no pooled connection (bound to
# a previous loop) is reused by the next test.
@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _db_clean():
    async with engine.begin() as conn:
        for tbl in ["events_outbox", "ledger_entries", "registrations", "sessions", "wallets", "users"]:
            try:
                await conn.exec_driver_sql(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE;")
            except Exception:
                pass
    yield
    # This is the key piece: drop all pooled connections so the next test
    # (with its own event loop) cannot reuse a connection from the previous loop.
    await engine.dispose()


@pytest_asyncio.fixture
async def db():
    async with SessionLocal() as s:
        yield s


# ---------- helpers ----------
async def mk_user(db, email: str, name: str) -> uuid.UUID:
    u = await users_repo.upsert_by_email(db, email=email, name=name, phone=None)
    await db.commit()
    return u.id

async def deposit(db, user_id: uuid.UUID, amount_cents: int):
    await ledger_repo.apply_ledger_entry(
        db,
        user_id=user_id,
        kind="deposit_in",
        amount_cents=amount_cents,
        idempotency_key=f"test-dep:{user_id}:{amount_cents}",
    )
    await db.commit()

async def mk_session(
    db, *, title: str, starts_at_utc: datetime, tz: str, capacity: int, fee_cents: int
) -> uuid.UUID:
    s = await sess_repo.create_session(
        db, title=title, starts_at_utc=starts_at_utc, timezone_name=tz, capacity=capacity, fee_cents=fee_cents
    )
    await db.commit()
    return s.id


# Disable redis queueing in tests to avoid event-loop issues.
@pytest_asyncio.fixture(autouse=True)
def _stub_promotion_queue(monkeypatch):
    # cancel_registration imports the symbol into its own module namespace,
    # so patch that symbol there.
    import app.services.cancellation as cancel_service

    async def _noop_enqueue(*args, **kwargs):
        return None

    monkeypatch.setattr(cancel_service, "enqueue_promotion_check", _noop_enqueue)
    yield




# Full fix: create a per-test Redis client on the current loop
# If you want the queue to run during tests, patch the client per test,
# so it’s created on the same loop and torn down before the loop closes.

# Add this to tests/conftest.py (alongside your other fixtures):
@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _redis_per_test(monkeypatch):
    settings = get_settings()
    client = from_url(settings.REDIS_URL, decode_responses=True)

    import app.redis_client as rc
    import app.services.promotion as promotion

    monkeypatch.setattr(rc, "redis", client, raising=True)
    monkeypatch.setattr(promotion, "redis", client, raising=True)

    try:
        await client.flushdb()
    except Exception:
        pass

    yield

    # Properly close for redis-py 5.x
    try:
        # redis>=5 uses aclose(); close() is deprecated
        await client.aclose()
    except AttributeError:
        # fallback for older redis versions
        await client.close()

    # Disconnect the pool (supports async in >=5)
    try:
        disc = client.connection_pool.disconnect
        if inspect.iscoroutinefunction(disc):
            await disc()
        else:
            disc()
    except Exception:
        pass

# If you’d rather avoid Redis entirely in tests, 
# you can instead stub the enqueue function (no warnings, no network):
@pytest_asyncio.fixture(autouse=True)
def _stub_promotion_queue(monkeypatch):
    import app.services.cancellation as cancel_service
    async def _noop_enqueue(*args, **kwargs): return None
    monkeypatch.setattr(cancel_service, "enqueue_promotion_check", _noop_enqueue)
    yield

    
# Keep your DB pool disposal fixture too (prevents cross-loop reuse of PG connections):    
@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _db_clean():
    from app.db import engine
    async with engine.begin() as conn:
        for tbl in ["events_outbox", "ledger_entries", "registrations", "sessions", "wallets", "users"]:
            try:
                await conn.exec_driver_sql(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE;")
            except Exception:
                pass
    yield
    await engine.dispose()


