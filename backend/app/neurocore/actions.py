"""PendingActionService — the only place a NeuroCore write tool's proposal
turns into a real backend mutation, and the only place that mutation ever
happens (see app.neurocore.tools.write_tools for where a PendingAction
gets created, and app.api.ai for the confirm/cancel/get/list routes).

Unlike DecisionService/ExecutionService/OptimizationService (which manage
their own database sessions internally, driven by the simulation tick
loop), this service takes `db: AsyncSession` as a parameter on every
method — the same pattern NeuroCoreService.chat already established for
this package, since every call here originates from a single HTTP
request's already-open, request-scoped session (see app.api.deps.get_db).

Idempotency: the PENDING -> CONFIRMED transition (see `confirm`) is a
single atomic, conditional SQL UPDATE (`WHERE status = 'PENDING'`, with
`.returning(...)` to detect whether it actually matched a row) — Postgres'
own row-level locking serializes two concurrent UPDATEs against the same
row, so at most one of two concurrent `confirm()` calls can ever see
`won=True`. Every step after that point (re-validation, calling the
*existing* SimulationService.execute_decision/.replay_scenario, recording
the outcome) only ever runs for whichever single request won the CAS —
this is what makes "two concurrent confirmation requests result in
exactly one execution" true by construction, not by convention.

Re-validation ("never trust an old plan blindly") re-reads the decision/
plan/rack/scenario from `simulation` (the live source of truth) at
confirmation time, never from what was true when the PendingAction row was
created.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.execution.service import action_type_for_rule_key
from app.models.audit_log import ActionAuditLog
from app.models.enums import DecisionStatus, ExecutionStatus, PendingActionStatus, PendingActionType
from app.models.pending_action import PendingAction
from app.schemas.pending_action import PendingActionRead
from app.utils.time import utcnow
from app.websocket.manager import manager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.neurocore.ports import SimulationPort

logger = logging.getLogger(__name__)

# How long an operator has to confirm before a proposed action goes stale
# and must be re-evaluated from scratch — short on purpose: telemetry
# moves every simulation tick, so a five-minute-old proposal is already at
# risk of describing a cluster state that no longer exists.
DEFAULT_EXPIRY_SECONDS = 300

_ACTIVE_PENDING_STATUSES = (PendingActionStatus.PENDING,)


class ActionStateConflict(Exception):
    """Raised when a PendingAction isn't in a state that permits the
    requested transition (already confirmed/cancelled/expired/completed/
    failed, or lost the confirm race to a concurrent request) — maps to
    HTTP 409 at the API layer, distinct from LookupError's 404.
    """


class PendingActionService:
    """Stateless — every fact it needs lives in the database or is passed
    in via `simulation` (see app.neurocore.ports.SimulationPort), so
    constructing a fresh instance per request/tool-call is cheap and
    correct; there is no in-memory cache to keep consistent.
    """

    # --- proposing an action (called from write tools) ----------------------

    async def create_for_decision(
        self,
        db: "AsyncSession",
        *,
        conversation_id: uuid.UUID,
        decision_id: uuid.UUID,
        simulation: "SimulationPort",
        now: datetime | None = None,
    ) -> PendingAction:
        """Raises LookupError if the decision doesn't exist, ValueError if
        it's not currently executable (see the same checks `confirm`
        performs again later — this is the "can I even propose this"
        gate, `confirm`'s re-validation is the "is it still true" gate).
        """
        now = now or utcnow()
        decision = simulation.get_decision(decision_id)
        if decision is None:
            raise LookupError(f"No decision found with id {decision_id}.")
        if decision.status not in (DecisionStatus.PENDING, DecisionStatus.ACCEPTED):
            raise ValueError(f"Decision '{decision_id}' is {decision.status.value} and cannot be executed.")
        if action_type_for_rule_key(decision.rule_key) is None:
            raise ValueError(f"Decision '{decision_id}' has no supported execution action.")

        target_rack_id = decision.affected_racks[0] if decision.affected_racks else None
        target_rack = simulation.rack_state(target_rack_id) if target_rack_id is not None else None
        target = target_rack.name if target_rack is not None else "the cluster"

        row = PendingAction(
            conversation_id=conversation_id,
            plan_id=decision.plan_id,
            decision_id=decision.id,
            action_type=PendingActionType.EXECUTE_DECISION,
            target=target,
            status=PendingActionStatus.PENDING,
            summary=_build_decision_confirmation_summary(decision),
            scenario_key=simulation.scenario_status.key,
            expires_at=now + timedelta(seconds=DEFAULT_EXPIRY_SECONDS),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        await self._audit(db, row, "proposed", success=True, result=row.summary)
        await self._broadcast(row, "AI_ACTION_PENDING")
        return row

    async def create_for_replay(
        self,
        db: "AsyncSession",
        *,
        conversation_id: uuid.UUID,
        simulation: "SimulationPort",
        now: datetime | None = None,
    ) -> PendingAction:
        now = now or utcnow()
        row = PendingAction(
            conversation_id=conversation_id,
            plan_id=None,
            decision_id=None,
            action_type=PendingActionType.REPLAY_SIMULATION,
            target="cluster",
            status=PendingActionStatus.PENDING,
            summary=_build_replay_confirmation_summary(),
            scenario_key=simulation.scenario_status.key,
            expires_at=now + timedelta(seconds=DEFAULT_EXPIRY_SECONDS),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        await self._audit(db, row, "proposed", success=True, result=row.summary)
        await self._broadcast(row, "AI_ACTION_PENDING")
        return row

    # --- reads ---------------------------------------------------------------

    async def get(self, db: "AsyncSession", action_id: uuid.UUID, *, now: datetime | None = None) -> PendingAction | None:
        now = now or utcnow()
        action = await db.get(PendingAction, action_id)
        if action is None:
            return None
        return await self._maybe_expire(db, action, now)

    async def list(
        self,
        db: "AsyncSession",
        *,
        conversation_id: uuid.UUID | None = None,
        status: PendingActionStatus | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[PendingAction]:
        now = now or utcnow()
        query = select(PendingAction).order_by(PendingAction.created_at.desc()).limit(limit)
        if conversation_id is not None:
            query = query.where(PendingAction.conversation_id == conversation_id)
        if status is not None:
            query = query.where(PendingAction.status == status)
        result = await db.execute(query)
        rows = list(result.scalars().all())
        return [await self._maybe_expire(db, row, now) for row in rows]

    # --- confirm / cancel ------------------------------------------------

    async def confirm(
        self, db: "AsyncSession", *, action_id: uuid.UUID, simulation: "SimulationPort", now: datetime | None = None
    ) -> PendingAction:
        """Raises LookupError if the action doesn't exist, ActionStateConflict
        if it isn't (or is no longer) confirmable. Otherwise always returns
        the action — COMPLETED or FAILED — never raises for a business-
        level execution failure (see module docstring: that's data, not a
        transport error, mirroring how POST /api/decisions/{id}/execute
        already behaves for a remediation that finds no viable target).
        """
        now = now or utcnow()

        won = await self._cas(
            db, action_id, from_statuses=_ACTIVE_PENDING_STATUSES, to_status=PendingActionStatus.CONFIRMED,
            now=now, require_not_expired=True,
        )
        action = await db.get(PendingAction, action_id)
        if action is None:
            raise LookupError(f"Pending action '{action_id}' not found.")

        if not won:
            if action.status == PendingActionStatus.PENDING and action.expires_at <= now:
                expired = await self._cas(
                    db, action_id, from_statuses=_ACTIVE_PENDING_STATUSES, to_status=PendingActionStatus.EXPIRED, now=now
                )
                if expired:
                    await db.refresh(action)
                    await self._broadcast(action, "AI_ACTION_EXPIRED")
                raise ActionStateConflict("This action has expired and can no longer be confirmed.")
            raise ActionStateConflict(f"This action is already {action.status.value} and cannot be confirmed.")

        action.confirmed_at = now
        await db.commit()
        await db.refresh(action)
        await self._broadcast(action, "AI_ACTION_CONFIRMED")
        await self._audit(db, action, "confirmed", success=True, result="Action confirmed; validating before execution.")

        error = self._revalidate(action, simulation, now)
        if error is not None:
            action = await self._set_status(db, action, PendingActionStatus.FAILED, now=now, error_message=error, completed_at=now)
            await self._audit(db, action, "rejected", success=False, result=error)
            await self._broadcast(action, "AI_ACTION_FAILED")
            return action

        action = await self._set_status(db, action, PendingActionStatus.EXECUTING, now=now)
        await self._broadcast(action, "AI_ACTION_EXECUTING")

        try:
            if action.action_type == PendingActionType.EXECUTE_DECISION:
                assert action.decision_id is not None
                await simulation.execute_decision(action.decision_id)
                execution = next((e for e in simulation.all_executions if e.decision_id == action.decision_id), None)
            else:
                await simulation.replay_scenario()
                execution = None
        except (LookupError, ValueError) as exc:
            action = await self._set_status(
                db, action, PendingActionStatus.FAILED, now=now, error_message=str(exc), completed_at=now
            )
            await self._audit(db, action, "execution_failed", success=False, result=str(exc))
            await self._broadcast(action, "AI_ACTION_FAILED")
            return action

        if execution is not None and execution.status == ExecutionStatus.FAILED:
            error_message = execution.error_message or "Execution failed."
            action = await self._set_status(
                db, action, PendingActionStatus.FAILED, now=now,
                error_message=error_message, execution_id=execution.id, completed_at=now,
            )
            await self._audit(db, action, "execution_failed", success=False, result=error_message)
            await self._broadcast(action, "AI_ACTION_FAILED")
            return action

        action = await self._set_status(
            db, action, PendingActionStatus.COMPLETED, now=now,
            execution_id=execution.id if execution is not None else None, completed_at=now,
        )
        result_text = (
            f"Execution completed (execution_id={execution.id})." if execution is not None else "Replay started successfully."
        )
        await self._audit(db, action, "executed", success=True, result=result_text)
        await self._broadcast(action, "AI_ACTION_COMPLETED")
        return action

    async def cancel(self, db: "AsyncSession", *, action_id: uuid.UUID, now: datetime | None = None) -> PendingAction:
        now = now or utcnow()
        won = await self._cas(db, action_id, from_statuses=_ACTIVE_PENDING_STATUSES, to_status=PendingActionStatus.CANCELLED, now=now)
        action = await db.get(PendingAction, action_id)
        if action is None:
            raise LookupError(f"Pending action '{action_id}' not found.")
        if not won:
            raise ActionStateConflict(f"This action is already {action.status.value} and cannot be cancelled.")

        await self._audit(db, action, "cancelled", success=True, result="Action cancelled by operator.")
        await self._broadcast(action, "AI_ACTION_CANCELLED")
        return action

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _revalidate(action: PendingAction, simulation: "SimulationPort", now: datetime) -> str | None:
        """Returns an explanation string if the action can no longer be
        executed, or None if it's still valid. See module docstring for
        why every check re-reads from `simulation` rather than trusting
        anything captured when the PendingAction was created.
        """
        if action.expires_at <= now:
            return "This action has expired."

        if action.action_type == PendingActionType.EXECUTE_DECISION:
            if action.decision_id is None:
                return "This action has no associated decision."
            decision = simulation.get_decision(action.decision_id)
            if decision is None:
                return "The decision behind this action no longer exists."
            if action.plan_id is not None and simulation.get_plan(action.plan_id) is None:
                return "The optimization plan behind this action no longer exists."
            if decision.status not in (DecisionStatus.PENDING, DecisionStatus.ACCEPTED):
                return f"The decision is now '{decision.status.value}' and can no longer be executed."
            target_rack_id = decision.affected_racks[0] if decision.affected_racks else None
            if target_rack_id is not None and simulation.rack_state(target_rack_id) is None:
                return "The target rack no longer exists."
            if action_type_for_rule_key(decision.rule_key) is None:
                return "This decision no longer maps to a supported execution action."
            current_scenario_key = simulation.scenario_status.key
            if current_scenario_key != action.scenario_key:
                return (
                    f"The active scenario has changed since this action was proposed "
                    f"('{action.scenario_key}' -> '{current_scenario_key}'); please re-evaluate before proceeding."
                )

        return None

    async def _maybe_expire(self, db: "AsyncSession", action: PendingAction, now: datetime) -> PendingAction:
        if action.status == PendingActionStatus.PENDING and action.expires_at <= now:
            won = await self._cas(db, action.id, from_statuses=_ACTIVE_PENDING_STATUSES, to_status=PendingActionStatus.EXPIRED, now=now)
            if won:
                await db.refresh(action)
                await self._broadcast(action, "AI_ACTION_EXPIRED")
        return action

    @staticmethod
    async def _cas(
        db: "AsyncSession", action_id: uuid.UUID, *, from_statuses: tuple, to_status: PendingActionStatus,
        now: datetime, require_not_expired: bool = False,
    ) -> bool:
        """The one atomic operation this whole service's idempotency
        guarantee rests on — see module docstring. `require_not_expired`
        additionally requires `expires_at > now` in the same conditional
        UPDATE — used only for the PENDING -> CONFIRMED transition, so
        confirming an already-timed-out action is its own explicit
        ActionStateConflict (see `confirm`) rather than silently falling
        through to the same bucket as a stale plan/decision/rack.
        """
        conditions = [PendingAction.id == action_id, PendingAction.status.in_(from_statuses)]
        if require_not_expired:
            conditions.append(PendingAction.expires_at > now)
        stmt = update(PendingAction).where(*conditions).values(status=to_status).returning(PendingAction.id)
        result = await db.execute(stmt)
        won = result.scalar_one_or_none() is not None
        await db.commit()
        return won

    @staticmethod
    async def _set_status(db: "AsyncSession", action: PendingAction, status: PendingActionStatus, *, now: datetime, **extra: object) -> PendingAction:
        """Plain (non-CAS) update — safe here because every call site is
        already the sole owner of `action` (it only runs after this
        request has won the PENDING -> CONFIRMED race), so there is no
        concurrent writer left to race against.
        """
        action.status = status
        for key, value in extra.items():
            setattr(action, key, value)
        await db.commit()
        await db.refresh(action)
        return action

    @staticmethod
    async def _audit(db: "AsyncSession", action: PendingAction, label: str, *, success: bool, result: str) -> None:
        row = ActionAuditLog(
            pending_action_id=action.id,
            action=label,
            conversation_id=action.conversation_id,
            plan_id=action.plan_id,
            decision_id=action.decision_id,
            execution_id=action.execution_id,
            result=result[:1000],
            success=success,
        )
        db.add(row)
        await db.commit()

    @staticmethod
    async def _broadcast(action: PendingAction, event_type: str) -> None:
        """Reuses the existing WebSocket connection manager (see
        app.websocket.manager) — no second WebSocket system. Distinct in
        shape from the regular per-tick TelemetrySnapshot broadcast (this
        carries a `"type"` discriminator); a frontend not yet aware of AI
        action events can simply ignore messages that don't look like a
        snapshot.
        """
        if manager.connection_count == 0:
            return
        payload = {"type": event_type, "action": PendingActionRead.model_validate(action).model_dump(mode="json")}
        try:
            await manager.broadcast(payload)
        except Exception:  # pragma: no cover - defensive, mirrors SimulationService's own tick-loop guard
            logger.exception("Failed to broadcast AI action event %s", event_type)


def _build_decision_confirmation_summary(decision) -> str:
    impact_bits: list[str] = []
    if decision.expected_temperature_reduction is not None:
        impact_bits.append(f"a {decision.expected_temperature_reduction:.1f}°C reduction")
    if decision.expected_power_saving is not None:
        impact_bits.append(f"a {decision.expected_power_saving:.1f} kW power saving")
    impact = " and ".join(impact_bits) if impact_bits else "a measurable improvement"
    return (
        f"I can execute the recommended action: {decision.recommended_action} "
        f"The selected plan predicts {impact} with {decision.confidence:.0f}% confidence. Proceed?"
    )


def _build_replay_confirmation_summary() -> str:
    return (
        "I can replay the most recently completed scenario from the beginning, re-running its telemetry "
        "sequence against the current cluster. Proceed?"
    )
