"""Smoke test for GET /health.

Does not require a live database/Redis to run: the endpoint performs real
connectivity checks and reports "disconnected"/503 truthfully when they are
unreachable, so this test only asserts the response is well-formed.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_health_returns_well_formed_payload(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code in (200, 503)

    body = response.json()
    assert body["status"] in ("healthy", "unhealthy")
    assert body["database"] in ("connected", "disconnected")
    assert body["redis"] in ("connected", "disconnected")
