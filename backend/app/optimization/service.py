"""OptimizationService — turns OptimizationEngine output into durable,
deduplicated OptimizationPlan rows and raises the objective's four
lifecycle events.

Mirrors DecisionService's role exactly: SimulationService and the REST API
only ever talk to this service, never to a concrete engine directly.
Swapping SimulationOptimizer for a reinforcement-learning-based engine
later means constructing OptimizationService with a different `engine=`
argument — nothing else changes. See app.optimization.base for the
OptimizationEngine contract.

Dedup mirrors DecisionService's rule_key mechanism, keyed by
OptimizationPlan.trigger_key: a rack that stays triggered across many
consecutive ticks gets ONE row, refreshed in place (same as a Decision does
while its rule keeps re-affirming), not a new row every tick — which is
also what keeps "Optimization Started"/"Plan Selected"/"Plan Rejected"
edge-detected (fired on creation or on a genuine change of winner) rather
than spammed every tick a condition merely continues to hold.

Not WebSocket-aware, on purpose (same separation as event_service.py and
app.ai.service) — this module persists and tracks state; SimulationService
broadcasts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.enums import EventSeverity, ExecutionActionType, OptimizationPlanStatus
from app.models.event import Event
from app.models.optimization_plan import OptimizationPlan as OptimizationPlanRow
from app.optimization.base import OptimizationContext, OptimizationEngine
from app.optimization.base import OptimizationPlan as OptimizationPlanData
from app.schemas.optimization import OptimizationCandidateRead
from app.services.event_service import EventDraft, persist_events
from app.simulation.state import ClusterState, RackState

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 200  # bounds the in-memory plan cache for a long-running process
RECENT_EVENTS_LIMIT = 20  # matches app.ai.service's own recent-events window


class OptimizationService:
    def __init__(self, engine: OptimizationEngine) -> None:
        self._engine = engine
        self._active: dict[str, uuid.UUID] = {}  # trigger_key -> plan id, for dedup
        self._cache: dict[uuid.UUID, OptimizationPlanRow] = {}

    # --- read access -------------------------------------------------------

    @property
    def active_plans(self) -> list[OptimizationPlanRow]:
        """Plans whose trigger_key is still active — what TelemetrySnapshot
        exposes, mirroring DecisionService.active_decisions.
        """
        ids = set(self._active.values())
        rows = [self._cache[pid] for pid in ids if pid in self._cache]
        return sorted(rows, key=lambda p: p.created_at, reverse=True)

    @property
    def all_plans(self) -> list[OptimizationPlanRow]:
        return sorted(self._cache.values(), key=lambda p: p.created_at, reverse=True)

    @property
    def latest_plan(self) -> OptimizationPlanRow | None:
        plans = self.all_plans
        return plans[0] if plans else None

    def get(self, plan_id: uuid.UUID) -> OptimizationPlanRow | None:
        return self._cache.get(plan_id)

    def reset(self) -> None:
        """Clears which plans currently count as *active* — called from
        app.simulation.engine.SimulationService.reset. Historical plans
        (`all_plans`/`get`/the underlying database rows) are untouched;
        only the active set that TelemetrySnapshot exposes is emptied, so
        a resumed simulation starts fresh rather than reporting a plan for
        a trigger that no longer exists post-reset.
        """
        self._active = {}

    # --- per-tick ---------------------------------------------------------

    async def tick(
        self,
        cluster: ClusterState,
        racks: list[RackState],
        scenario_key: str,
        forecasts: dict,
        cluster_db_id: uuid.UUID,
        scenario_db_id: uuid.UUID | None,
        now: datetime,
    ) -> tuple[dict[uuid.UUID, OptimizationPlanData], list[Event]]:
        """Plan for every rack the engine finds a trigger for, persist,
        raise lifecycle events, and return this tick's plans keyed by
        trigger rack id — DecisionService consumes this directly (see
        app.simulation.engine._tick), the same way it already consumes
        ForecastService's output.
        """
        async with AsyncSessionLocal() as db:
            recent_events = await self._recent_events(db)

        context = OptimizationContext(
            cluster=cluster,
            racks=racks,
            scenario_key=scenario_key,
            scenario_active=scenario_key != "normal",
            forecasts=forecasts,
            recent_events=recent_events,
            now=now,
        )
        plans = self._engine.plan(context)
        if not plans:
            self._clear_stale(seen_keys=set())
            return {}, []

        plans_by_rack: dict[uuid.UUID, OptimizationPlanData] = {}
        event_drafts: list[EventDraft] = []

        async with AsyncSessionLocal() as db:
            for plan in plans:
                row, is_new, winner_changed = await self._upsert(db, plan, cluster_db_id, scenario_db_id)
                # Keep the in-memory plan's id in sync with the persisted
                # (possibly pre-existing, refreshed-in-place) row, so a
                # DecisionDraft built from this plan this tick points at a
                # real, current OptimizationPlan id — see app.ai.rules.
                plan = replace(plan, id=row.id)
                plans_by_rack[plan.trigger_rack_id] = plan

                if is_new:
                    event_drafts.append(self._draft(row, "Optimization Started", plan.trigger_reason))
                    event_drafts.append(self._draft(row, "Optimization Completed", _completed_message(plan)))
                if is_new or winner_changed:
                    event_drafts.append(self._selection_draft(row, plan))

            self._clear_stale(seen_keys={plan.trigger_key for plan in plans})
            persisted = await persist_events(db, event_drafts) if event_drafts else []

        for event in persisted:
            logger.info("Optimization event: [%s] %s", event.severity.value, event.title)
        return plans_by_rack, persisted

    # --- internals -----------------------------------------------------------

    async def _upsert(
        self, db, plan: OptimizationPlanData, cluster_db_id: uuid.UUID, scenario_db_id: uuid.UUID | None
    ) -> tuple[OptimizationPlanRow, bool, bool]:
        """Returns (row, is_new, winner_changed)."""
        winner = plan.winner
        candidates_json = [
            OptimizationCandidateRead.model_validate(c).model_dump(mode="json") for c in plan.candidates
        ]

        existing_id = self._active.get(plan.trigger_key)
        if existing_id is not None:
            row = await db.get(OptimizationPlanRow, existing_id)
            if row is not None:
                previous_winner = row.winner_action_type
                row.trigger_reason = plan.trigger_reason
                row.candidates = candidates_json
                row.winner_action_type = winner.action_type
                row.winner_overall_score = winner.score.overall_score
                row.winner_confidence = winner.score.confidence
                row.completed_at = plan.now
                await db.commit()
                await db.refresh(row)
                self._remember(row)
                return row, False, previous_winner != winner.action_type
            # Mapping was stale — fall through and create a fresh row.
            self._active.pop(plan.trigger_key, None)

        row = OptimizationPlanRow(
            id=plan.id,
            cluster_id=cluster_db_id,
            scenario_id=scenario_db_id,
            trigger_rack_id=plan.trigger_rack_id,
            trigger_key=plan.trigger_key,
            trigger_reason=plan.trigger_reason,
            status=OptimizationPlanStatus.COMPLETED,
            candidates=candidates_json,
            winner_action_type=winner.action_type,
            winner_overall_score=winner.score.overall_score,
            winner_confidence=winner.score.confidence,
            completed_at=plan.now,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        self._active[plan.trigger_key] = row.id
        self._remember(row)
        return row, True, True

    def _clear_stale(self, seen_keys: set[str]) -> None:
        """A trigger_key that didn't appear in this tick's plans means that
        rack recovered/stopped triggering — drop it from `_active` so a
        future re-trigger starts a fresh plan (and fresh lifecycle events)
        rather than silently reusing an old row. The row itself stays in
        `_cache`/the database as history; nothing is deleted.
        """
        for trigger_key in list(self._active):
            if trigger_key not in seen_keys:
                self._active.pop(trigger_key, None)

    def _remember(self, row: OptimizationPlanRow) -> None:
        self._cache[row.id] = row
        if len(self._cache) > MAX_CACHE_SIZE:
            oldest_id = next(iter(self._cache))
            if oldest_id != row.id:
                self._cache.pop(oldest_id, None)

    @staticmethod
    def _draft(row: OptimizationPlanRow, title: str, message: str) -> EventDraft:
        return EventDraft(
            cluster_id=row.cluster_id,
            rack_id=row.trigger_rack_id,
            scenario_id=row.scenario_id,
            severity=EventSeverity.INFO,
            title=title,
            message=message,
        )

    @classmethod
    def _selection_draft(cls, row: OptimizationPlanRow, plan: OptimizationPlanData) -> EventDraft:
        winner = plan.winner
        if winner.action_type == ExecutionActionType.NO_ACTION:
            message = (
                f"No remediation warranted — evaluated {len(plan.candidates)} candidate action(s); "
                f"the best available still only scored {winner.score.overall_score:.0f}/100."
            )
            return cls._draft(row, "Plan Rejected", message)

        message = (
            f"Selected: {winner.description} (score {winner.score.overall_score:.0f}/100, "
            f"{winner.score.confidence:.0f}% confidence)."
        )
        return cls._draft(row, "Plan Selected", message)

    @staticmethod
    async def _recent_events(db: AsyncSession) -> list[Event]:
        """Mirrors app.ai.service.DecisionService._recent_events — recent
        history is one of the objective's stated planning inputs (used by
        app.optimization.planner._recent_attempt_count to discount
        confidence for an action that was just tried).
        """
        result = await db.execute(select(Event).order_by(Event.occurred_at.desc()).limit(RECENT_EVENTS_LIMIT))
        return list(result.scalars().all())


def _completed_message(plan: OptimizationPlanData) -> str:
    winner = plan.winner
    return (
        f"Evaluated {len(plan.candidates)} candidate action(s) for the trigger; "
        f"best option: {winner.description} (score {winner.score.overall_score:.0f}/100)."
    )
