"""DecisionService — turns DecisionEngine output into durable,
deduplicated, lifecycle-managed Decision rows.

This is the layer that stays constant when the reasoning *strategy*
changes: SimulationService and the REST API only ever talk to
DecisionService, never to a concrete engine directly. Swapping
RuleBasedDecisionEngine for an LLMDecisionEngine later means constructing
DecisionService with a different `engine=` argument (dependency injection)
— nothing else in the app changes. See app.ai.base for the DecisionEngine
contract itself.

Not WebSocket-aware, on purpose (same separation as event_service.py):
this module persists and tracks state; SimulationService is responsible
for broadcasting. "Decision updated" (e.g. a confidence change) needs no
special broadcast path at all — decisions are part of every regular
per-tick TelemetrySnapshot, so an update is visible on the very next tick.
Only discrete lifecycle transitions (created / expired / accepted /
rejected / executed) raise a persisted Event.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import DecisionContext, DecisionDraft, DecisionEngine
from app.db.session import AsyncSessionLocal
from app.models.decision import Decision
from app.models.enums import DecisionStatus, EventSeverity
from app.models.event import Event
from app.services.event_service import EventDraft, persist_events
from app.simulation.state import ClusterState, RackState

if TYPE_CHECKING:
    from app.forecasting.base import RackPrediction

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {DecisionStatus.PENDING, DecisionStatus.ACCEPTED}
RECENT_EVENTS_LIMIT = 20
MAX_CACHE_SIZE = 200  # bounds the in-memory decision cache for a long-running process


class DecisionService:
    """Owns decision persistence, deduplication, expiry, and the
    accept/reject/execute lifecycle. Takes a DecisionEngine via
    constructor injection.
    """

    def __init__(self, engine: DecisionEngine) -> None:
        self._engine = engine
        self._active: dict[str, uuid.UUID] = {}  # rule_key -> decision id, for dedup
        self._cache: dict[uuid.UUID, Decision] = {}  # id -> last known row (FIFO-bounded)

    # --- read access -------------------------------------------------------

    @property
    def active_decisions(self) -> list[Decision]:
        """Currently pending/accepted decisions — what TelemetrySnapshot exposes."""
        decisions = [self._cache[did] for did in self._active.values() if did in self._cache]
        return sorted(decisions, key=lambda d: d.timestamp, reverse=True)

    @property
    def all_decisions(self) -> list[Decision]:
        """Every decision this process has seen (any status), most recent first."""
        return sorted(self._cache.values(), key=lambda d: d.timestamp, reverse=True)

    def get(self, decision_id: uuid.UUID) -> Decision | None:
        return self._cache.get(decision_id)

    # --- evaluation: called once per simulation tick ------------------------

    async def evaluate(
        self,
        cluster: ClusterState,
        racks: list[RackState],
        scenario_key: str,
        scenario_target_rack_id: uuid.UUID | None,
        cluster_db_id: uuid.UUID,
        scenario_db_id: uuid.UUID | None,
        now: datetime,
        forecasts: dict[uuid.UUID, list["RackPrediction"]] | None = None,
    ) -> list[Event]:
        """Evaluate the cluster, upsert (dedupe/update) active decisions,
        and expire stale ones. Returns the lifecycle events raised this
        call (Created / Expired) for the caller to fold into its own
        broadcast — confidence-only updates raise no event, see module
        docstring.

        `forecasts` is ForecastService's latest output (see
        app.forecasting) — optional and defaulted so this stays callable
        exactly as before anywhere forecasts aren't available yet.
        """
        async with AsyncSessionLocal() as db:
            recent_events = await self._recent_events(db)
            context = DecisionContext(
                cluster=cluster,
                racks=racks,
                scenario_key=scenario_key,
                scenario_target_rack_id=scenario_target_rack_id,
                recent_events=recent_events,
                now=now,
                forecasts=forecasts or {},
            )
            drafts = self._engine.evaluate(context)

            event_drafts: list[EventDraft] = []
            seen_rule_keys: set[str] = set()

            for draft in drafts:
                seen_rule_keys.add(draft.rule_key)
                created = await self._upsert(db, draft, cluster_db_id, scenario_db_id, now)
                if created is not None:
                    event_drafts.append(self._lifecycle_draft(created, "Decision Created"))

            for decision in await self._expire_stale(db, seen_rule_keys, now):
                event_drafts.append(self._lifecycle_draft(decision, "Decision Expired"))

            persisted = await persist_events(db, event_drafts) if event_drafts else []

        for event in persisted:
            logger.info("Decision event: [%s] %s", event.severity.value, event.title)
        return persisted

    # --- REST-triggered lifecycle transitions -------------------------------

    async def accept(self, decision_id: uuid.UUID) -> tuple[Decision, list[Event]]:
        return await self._transition(decision_id, DecisionStatus.ACCEPTED, "Decision Accepted", release=False)

    async def reject(self, decision_id: uuid.UUID) -> tuple[Decision, list[Event]]:
        return await self._transition(decision_id, DecisionStatus.REJECTED, "Decision Rejected", release=True)

    async def execute(self, decision_id: uuid.UUID) -> tuple[Decision, list[Event]]:
        """Updates decision state only. Actual remediation (e.g. really
        migrating a workload) is out of scope for now, per the objective.
        """
        return await self._transition(decision_id, DecisionStatus.EXECUTED, "Decision Executed", release=True)

    async def _transition(
        self, decision_id: uuid.UUID, new_status: DecisionStatus, event_title: str, *, release: bool
    ) -> tuple[Decision, list[Event]]:
        async with AsyncSessionLocal() as db:
            row = await db.get(Decision, decision_id)
            if row is None:
                raise LookupError(f"Decision '{decision_id}' not found.")
            if row.status not in _ACTIVE_STATUSES:
                raise ValueError(f"Decision '{decision_id}' is already {row.status.value} and cannot be changed.")

            row.status = new_status
            await db.commit()
            await db.refresh(row)

            persisted = await persist_events(db, [self._lifecycle_draft(row, event_title)])

        self._remember(row)
        if release:
            self._active.pop(row.rule_key, None)

        return row, persisted

    # --- internals -----------------------------------------------------------

    async def _upsert(
        self,
        db: AsyncSession,
        draft: DecisionDraft,
        cluster_db_id: uuid.UUID,
        scenario_db_id: uuid.UUID | None,
        now: datetime,
    ) -> Decision | None:
        """Returns the new Decision row if one was created, or None if an
        existing active decision for this rule_key was updated in place
        (the dedup path — "avoid duplicates").
        """
        existing_id = self._active.get(draft.rule_key)
        if existing_id is not None:
            row = await db.get(Decision, existing_id)
            if row is not None and row.status in _ACTIVE_STATUSES:
                row.confidence = draft.confidence
                row.reasoning = draft.reasoning
                row.recommended_action = draft.recommended_action
                row.expected_temperature_reduction = draft.expected_temperature_reduction
                row.expected_power_saving = draft.expected_power_saving
                row.expires_at = now + timedelta(seconds=draft.ttl_seconds)
                await db.commit()
                await db.refresh(row)
                self._remember(row)
                return None
            # Mapping was stale (resolved via a REST call since) — fall
            # through and create a fresh decision for this rule_key.
            self._active.pop(draft.rule_key, None)

        row = Decision(
            cluster_id=cluster_db_id,
            scenario_id=scenario_db_id,
            rule_key=draft.rule_key,
            severity=draft.severity,
            title=draft.title,
            reasoning=draft.reasoning,
            recommended_action=draft.recommended_action,
            expected_temperature_reduction=draft.expected_temperature_reduction,
            expected_power_saving=draft.expected_power_saving,
            confidence=draft.confidence,
            affected_racks=draft.affected_racks,
            affected_jobs=[],  # placeholder — no Job model exists yet
            status=DecisionStatus.PENDING,
            expires_at=now + timedelta(seconds=draft.ttl_seconds),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        self._active[draft.rule_key] = row.id
        self._remember(row)
        return row

    async def _expire_stale(self, db: AsyncSession, seen_rule_keys: set[str], now: datetime) -> list[Decision]:
        """A decision whose rule stopped firing this tick isn't yanked
        immediately — only once its sliding expiry (last refreshed while
        still being re-affirmed) elapses, so a single missed tick doesn't
        cause flapping.
        """
        expired: list[Decision] = []
        for rule_key in list(self._active):
            if rule_key in seen_rule_keys:
                continue

            decision_id = self._active[rule_key]
            row = await db.get(Decision, decision_id)
            if row is None or row.status not in _ACTIVE_STATUSES:
                self._active.pop(rule_key, None)
                continue

            if row.expires_at is not None and now >= row.expires_at:
                row.status = DecisionStatus.EXPIRED
                await db.commit()
                await db.refresh(row)
                self._active.pop(rule_key, None)
                self._remember(row)
                expired.append(row)

        return expired

    def _remember(self, decision: Decision) -> None:
        self._cache[decision.id] = decision
        if len(self._cache) > MAX_CACHE_SIZE:
            oldest_id = next(iter(self._cache))
            if oldest_id != decision.id:
                self._cache.pop(oldest_id, None)

    @staticmethod
    def _lifecycle_draft(decision: Decision, title: str) -> EventDraft:
        return EventDraft(
            cluster_id=decision.cluster_id,
            rack_id=decision.affected_racks[0] if decision.affected_racks else None,
            scenario_id=decision.scenario_id,
            severity=EventSeverity.INFO,
            title=title,
            message=f"{decision.title} — {decision.recommended_action}",
        )

    @staticmethod
    async def _recent_events(db: AsyncSession) -> list[Event]:
        result = await db.execute(select(Event).order_by(Event.occurred_at.desc()).limit(RECENT_EVENTS_LIMIT))
        return list(result.scalars().all())
