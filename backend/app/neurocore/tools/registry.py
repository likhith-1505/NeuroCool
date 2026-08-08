"""The fixed set of tools NeuroCore may ever offer a provider. There is no
"register a tool at runtime" mechanism and no generic "execute code" tool
— every entry here is an explicit, typed class the backend authored (see
app.neurocore.tools.base.Tool). Unknown tool names are rejected by
app.neurocore.tools.executor.execute_tool_call, never silently ignored or
dispatched to something not in this tuple.
"""

from __future__ import annotations

from app.neurocore.providers.base import ToolSpec
from app.neurocore.tools.base import Tool
from app.neurocore.tools.read_tools import (
    ReadClusterStateTool,
    ReadDecisionTool,
    ReadExecutionHistoryTool,
    ReadForecastTool,
    ReadOptimizationPlanTool,
    ReadRackTool,
    ReadRecentEventsTool,
)
from app.neurocore.tools.write_tools import ExecuteDecisionTool, ReplaySimulationTool

ALL_TOOLS: tuple[Tool, ...] = (
    ReadClusterStateTool(),
    ReadRackTool(),
    ReadForecastTool(),
    ReadOptimizationPlanTool(),
    ReadDecisionTool(),
    ReadRecentEventsTool(),
    ReadExecutionHistoryTool(),
    ExecuteDecisionTool(),
    ReplaySimulationTool(),
)

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def get_tool(name: str) -> Tool | None:
    return TOOLS_BY_NAME.get(name)


def tool_specs() -> list[ToolSpec]:
    """The provider-agnostic tool definitions sent on every tool-enabled
    generate() call — see app.neurocore.providers.base.ToolSpec.
    """
    return [
        ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema.model_json_schema())
        for tool in ALL_TOOLS
    ]
