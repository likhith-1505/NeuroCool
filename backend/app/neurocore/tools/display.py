"""Derives a human-readable PascalCase display name for a tool from its
internal snake_case registry name (e.g. "read_forecast" -> "ReadForecast")
— used only for user-facing stream events (see app.schemas.ai_stream's
ToolStartedEvent/ToolCompletedEvent), purely cosmetic. Never used for tool
dispatch — app.neurocore.tools.registry.get_tool always dispatches by the
exact registry name, not this derived one.
"""

from __future__ import annotations


def tool_display_name(name: str) -> str:
    return "".join(word.capitalize() for word in name.split("_")) or name
