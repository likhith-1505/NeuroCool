"""Imports every ORM model so Base.metadata is fully populated.

Alembic's env.py imports this module (not the individual model files) so
that autogenerate can see the complete schema.
"""

from app.db.base_class import Base
from app.models.cluster import Cluster
from app.models.conversation import Conversation, ConversationMessage
from app.models.decision import Decision
from app.models.event import Event
from app.models.execution import Execution
from app.models.optimization_plan import OptimizationPlan
from app.models.rack import Rack
from app.models.scenario import Scenario
from app.models.telemetry import TelemetryRecord

__all__ = [
    "Base",
    "Cluster",
    "Rack",
    "TelemetryRecord",
    "Scenario",
    "Event",
    "Decision",
    "Execution",
    "OptimizationPlan",
    "Conversation",
    "ConversationMessage",
]
