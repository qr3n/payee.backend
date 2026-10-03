import contextlib
import os
from collections.abc import AsyncGenerator, Generator

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db, get_redis
from app.main import app
from app.modules.accounts.models import TelegramAccount  # noqa: F401
from app.modules.payments.models import Payment  # noqa: F401

_test_container = None
_db_url = os.getenv("TEST_DATABASE_URL")

# Spin up dynamic PostgreSQL container if USE_TESTCONTAINERS is enabled
if not _db_url and os.getenv("USE_TESTCONTAINERS", "false").lower() in ("true", "1"):
    try:
        from testcontainers.postgres import PostgresContainer

        _test_container = PostgresContainer("postgres:17-alpine")
        _test_container.start()
        raw_url = _test_container.get_connection_url()
        _db_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://", 1
        )
    except Exception:
        _db_url = None

# Configure engine: Postgres (external / testcontainer) or fast in-memory SQLite
if _db_url:
    from sqlalchemy.pool import NullPool

    test_engine = create_async_engine(
        _db_url, echo=False, future=True, poolclass=NullPool
    )
else:
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
        poolclass=StaticPool,
    )

test_session_maker = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(scope="session", autouse=True)
def cleanup_testcontainers() -> Generator[None, None, None]:
    """Ensure Testcontainers resources are released after test session completes."""
    yield
    if _test_container is not None:
        with contextlib.suppress(Exception):
            _test_container.stop()


@pytest.fixture(autouse=True)
async def init_test_db() -> AsyncGenerator[None, None]:
    """Ensure database tables exist before test execution and clean data after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    if not _db_url:
        async with test_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
    else:
        # In real PostgreSQL, truncate/delete row data rather than dropping tables
        # to prevent corrupting development/migration schema state
        async with test_engine.begin() as conn:
            for table in reversed(SQLModel.metadata.sorted_tables):
                await conn.execute(table.delete())


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated database session for direct model manipulation in tests."""
    async with test_session_maker() as session:
        yield session


@pytest.fixture
async def fake_redis() -> AsyncGenerator[Redis, None]:
    """Provide an isolated in-memory FakeRedis instance for tests."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def setup_test_redis(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Redis
) -> AsyncGenerator[None, None]:
    """Ensure all core redis functions point to fake_redis during tests."""
    from app.modules.payments.scenarios import (
        MockBotScenario,
        scenario_registry,
    )

    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)
    monkeypatch.setattr("app.core.redis.get_redis_client", lambda: fake_redis)
    monkeypatch.setattr("app.core.db.async_session_maker", test_session_maker)
    scenario_registry.register(MockBotScenario())
    yield


@pytest.fixture
async def client(
    db_session: AsyncSession, fake_redis: Redis
) -> AsyncGenerator[AsyncClient, None]:
    """Async test client with get_db and get_redis dependencies overridden."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    async def override_get_redis() -> AsyncGenerator[Redis, None]:
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
