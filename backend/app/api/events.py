"""Read-only recent-events endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.event import Event
from app.schemas.event import EventRead

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=list[EventRead])
async def list_recent_events(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[EventRead]:
    result = await db.execute(select(Event).order_by(Event.occurred_at.desc()).limit(limit))
    events = result.scalars().all()
    return [EventRead.model_validate(event) for event in events]
