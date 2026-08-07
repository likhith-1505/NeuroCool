"""Turning telemetry transitions into durable Event rows.

Kept separate from the simulation engine so the engine only has to say
"here is the rack's previous state and its new state" — this module owns
the business rules for what counts as significant and how it's recorded.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EventSeverity, RackStatus
from app.models.event import Event
from app.simulation.state import RackState
from app.utils.time import utcnow

# --- Thresholds ----------------------------------------------------------

TEMPERATURE_WARNING_C = 80.0
TEMPERATURE_CRITICAL_C = 90.0
POWER_SPIKE_DELTA_KW = 3.5
COOLING_STABILIZED_THRESHOLD = 50.0

_STATUS_RANK: dict[RackStatus, int] = {
    RackStatus.HEALTHY: 0,
    RackStatus.WARNING: 1,
    RackStatus.CRITICAL: 2,
    RackStatus.OFFLINE: 3,
}


@dataclass(frozen=True)
class EventDraft:
    """An event that has been detected but not yet written to the database."""

    cluster_id: uuid.UUID
    rack_id: uuid.UUID | None
    severity: EventSeverity
    title: str
    message: str
    # Which scenario (if any) was active when this happened. Left for the
    # caller to fill in (see app.simulation.engine) — detection here stays
    # scenario-agnostic, it only knows about telemetry transitions.
    scenario_id: uuid.UUID | None = None


def detect_rack_events(cluster_id: uuid.UUID, previous: RackState, current: RackState) -> list[EventDraft]:
    """Compare a rack's telemetry across one tick and return any events
    the transition warrants. Pure/deterministic — easy to test in isolation.
    """
    events: list[EventDraft] = []

    events.extend(_temperature_events(cluster_id, previous, current))
    events.extend(_status_events(cluster_id, previous, current))
    events.extend(_power_spike_events(cluster_id, previous, current))
    events.extend(_cooling_events(cluster_id, previous, current))

    return events


def _temperature_events(cluster_id: uuid.UUID, previous: RackState, current: RackState) -> list[EventDraft]:
    if previous.temperature < TEMPERATURE_CRITICAL_C <= current.temperature:
        return [
            EventDraft(
                cluster_id,
                current.id,
                EventSeverity.CRITICAL,
                f"{current.name} temperature critical",
                f"{current.name} reached {current.temperature:.1f}°C, above the "
                f"{TEMPERATURE_CRITICAL_C:.0f}°C critical threshold.",
            )
        ]
    if previous.temperature < TEMPERATURE_WARNING_C <= current.temperature:
        return [
            EventDraft(
                cluster_id,
                current.id,
                EventSeverity.WARNING,
                f"{current.name} temperature exceeded threshold",
                f"{current.name} reached {current.temperature:.1f}°C, above the "
                f"{TEMPERATURE_WARNING_C:.0f}°C warning threshold.",
            )
        ]
    if previous.temperature >= TEMPERATURE_WARNING_C > current.temperature:
        return [
            EventDraft(
                cluster_id,
                current.id,
                EventSeverity.INFO,
                f"{current.name} temperature normalized",
                f"{current.name} cooled back to {current.temperature:.1f}°C, below the warning threshold.",
            )
        ]
    return []


def _status_events(cluster_id: uuid.UUID, previous: RackState, current: RackState) -> list[EventDraft]:
    if current.status == previous.status:
        return []

    degraded = _STATUS_RANK[current.status] > _STATUS_RANK[previous.status]
    if degraded:
        severity = EventSeverity.CRITICAL if current.status == RackStatus.CRITICAL else EventSeverity.WARNING
        title = f"{current.name} health degraded"
        message = (
            f"{current.name} moved from {previous.status.value} to {current.status.value} "
            f"(health {current.health_score:.0f}/100)."
        )
    else:
        severity = EventSeverity.INFO
        title = f"{current.name} recovered"
        message = (
            f"{current.name} improved from {previous.status.value} to {current.status.value} "
            f"(health {current.health_score:.0f}/100)."
        )

    return [EventDraft(cluster_id, current.id, severity, title, message)]


def _power_spike_events(cluster_id: uuid.UUID, previous: RackState, current: RackState) -> list[EventDraft]:
    if current.power_draw - previous.power_draw >= POWER_SPIKE_DELTA_KW:
        return [
            EventDraft(
                cluster_id,
                current.id,
                EventSeverity.WARNING,
                f"{current.name} power spike",
                f"{current.name} power draw jumped to {current.power_draw:.1f} kW "
                f"(+{current.power_draw - previous.power_draw:.1f} kW in one tick).",
            )
        ]
    return []


def _cooling_events(cluster_id: uuid.UUID, previous: RackState, current: RackState) -> list[EventDraft]:
    if previous.cooling_efficiency < COOLING_STABILIZED_THRESHOLD <= current.cooling_efficiency:
        return [
            EventDraft(
                cluster_id,
                current.id,
                EventSeverity.INFO,
                f"{current.name} cooling stabilized",
                f"{current.name} cooling efficiency recovered to {current.cooling_efficiency:.0f}%.",
            )
        ]
    return []


async def persist_events(db: AsyncSession, drafts: list[EventDraft]) -> list[Event]:
    """Write event drafts to the database and return the persisted rows."""
    if not drafts:
        return []

    occurred_at = utcnow()
    rows = [
        Event(
            cluster_id=draft.cluster_id,
            rack_id=draft.rack_id,
            scenario_id=draft.scenario_id,
            severity=draft.severity,
            title=draft.title,
            message=draft.message,
            occurred_at=occurred_at,
        )
        for draft in drafts
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows
