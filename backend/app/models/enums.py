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
    WORKLOAD_MIGRATION = "workload_migration"
    COOLING_ADJUSTMENT = "cooling_adjustment"
    JOB_DELAY = "job_delay"
    CLUSTER_REBALANCE = "cluster_rebalance"


class ExecutionStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
