"""
Async database setup. Works against both SQLite (local dev, zero setup) and
Postgres/Supabase (production) purely by changing DATABASE_URL — no other
code in the app needs to know which one is in use.
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Every ORM model inherits from this."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding one AsyncSession per request.
    Always rolls back on error and always closes the session, even if a
    handler raises — this is the one place session lifecycle is managed,
    so no router/service/repository needs to worry about it.
    """
    session = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def _add_missing_columns(sync_conn) -> None:
    """create_all() only creates tables that don't exist yet — it never
    alters an existing table, so a column added to a model after the first
    run (like User.subscription_product_id, added for the tiered
    subscription system) would silently never appear on an existing dev/
    prod database. Swap this whole function for real Alembic migrations
    before this matters for a production database with real user data;
    for now it's a deliberately tiny, dialect-generic "add column if
    missing" shim so the existing plant_companion.db (and its real test
    accounts/plants) doesn't need to be dropped every time a model gains a
    field."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    table_names = inspector.get_table_names()

    if "users" in table_names:
        existing = {col["name"] for col in inspector.get_columns("users")}
        if "subscription_product_id" not in existing:
            sync_conn.execute(text("ALTER TABLE users ADD COLUMN subscription_product_id VARCHAR(255)"))

    if "plants" in table_names:
        existing = {col["name"] for col in inspector.get_columns("plants")}
        if "status" not in existing:
            # server_default so every pre-existing row backfills to
            # "active" — no plant a user already owns should silently
            # vanish from their garden into an empty-string/null status.
            sync_conn.execute(text("ALTER TABLE plants ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT 'active'"))
        if "growth_background" not in existing:
            sync_conn.execute(text("ALTER TABLE plants ADD COLUMN growth_background VARCHAR(1024)"))
        if "regional_names" not in existing:
            sync_conn.execute(text("ALTER TABLE plants ADD COLUMN regional_names JSON"))
        if "soil_type" not in existing:
            sync_conn.execute(text("ALTER TABLE plants ADD COLUMN soil_type VARCHAR(255)"))
        if "soil_amendments" not in existing:
            sync_conn.execute(text("ALTER TABLE plants ADD COLUMN soil_amendments VARCHAR(255)"))

    if "subscriptions" in table_names:
        existing = {col["name"] for col in inspector.get_columns("subscriptions")}
        if "provider_subscription_id" not in existing:
            sync_conn.execute(text("ALTER TABLE subscriptions ADD COLUMN provider_subscription_id VARCHAR(255)"))


async def init_db() -> None:
    """Creates all tables on startup. Fine for a dev/experiment scale app;
    swap for Alembic migrations before a production launch with real users,
    since create_all() has no concept of altering an existing table."""
    async with engine.begin() as conn:
        from app.models import analytics, billing, plant, user  # noqa: F401 — ensures models are registered
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
