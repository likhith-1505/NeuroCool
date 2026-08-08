import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db() -> AsyncSession:
    """A real database session, for the handful of tests that need to
    verify actual transactional/concurrency behavior (see
    app.neurocore.actions.PendingActionService's idempotency guarantee) —
    every other test in this suite is pure logic and needs no database at
    all. Skips (rather than fails) when Postgres isn't reachable, so the
    rest of the suite stays runnable without one; the podman-based
    verification this project always runs before committing does have a
    real Postgres attached, which is where these tests actually run.
    """
    from app.db.session import AsyncSessionLocal, engine

    # pytest-asyncio gives each test function its own fresh event loop by
    # default; asyncpg's pooled connections are bound to whichever loop
    # first acquired them. Disposing the pool here drops any connection
    # left over from a *previous* test's loop, so this test's first
    # checkout is always freshly bound to *its own* loop instead of
    # raising "Future attached to a different loop".
    await engine.dispose()

    session = AsyncSessionLocal()
    try:
        await session.connection()
    except Exception as exc:
        await session.close()
        pytest.skip(f"Postgres not reachable for DB-backed tests: {exc}")
    try:
        yield session
    finally:
        await session.close()
