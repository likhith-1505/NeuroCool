"""Shared enum types used by ORM models."""

import enum


class RackStatus(str, enum.Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    OFFLINE = "offline"


class EventSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DecisionStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class ExecutionActionType(str, enum.Enum):
    """The kind of remediation action.

    Also reused as-is for app.optimization.base.OptimizationCandidate.
    action_type — a candidate the planner scores IS a description of "what
    execution action would this be", so giving it its own parallel enum
    would just be the same four-to-six values duplicated. FAN_OVERRIDE and
    NO_ACTION exist only because the Optimization Engine considers them as
    candidates: NO_ACTION is never actually executed (ExecutionService
    never receives a decision whose winning candidate was NO_ACTION), and
    FAN_OVERRIDE is executed exactly like the other four (see
    app.execution.manager.EFFECTS).
    """

    WORKLOAD_MIGRATION = "workload_migration"
    COOLING_ADJUSTMENT = "cooling_adjustment"
    JOB_DELAY = "job_delay"
    CLUSTER_REBALANCE = "cluster_rebalance"
    FAN_OVERRIDE = "fan_override"
    NO_ACTION = "no_action"


class ExecutionStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class OptimizationPlanStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class ConversationRole(str, enum.Enum):
    """Who authored one ConversationMessage — see app.models.conversation
    and app.neurocore.
    """

    USER = "user"
    ASSISTANT = "assistant"


class PendingActionType(str, enum.Enum):
    """What a PendingAction, once confirmed, actually does — see
    app.neurocore.actions. Both map onto an *existing* backend entry
    point (SimulationService.execute_decision / .replay_scenario); a
    PendingAction never invents a new mutation path.
    """

    EXECUTE_DECISION = "execute_decision"
    REPLAY_SIMULATION = "replay_simulation"


class PendingActionStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
