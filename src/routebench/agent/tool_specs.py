"""Converts AnalysisTool registry into Anthropic tool-use specifications."""

from __future__ import annotations

from typing import Any

from routebench.analysis.tools import TOOLS, AnalysisTool


def build_tool_specs(
    available_tools: list[AnalysisTool] | None = None,
) -> list[dict[str, Any]]:
    """Convert analysis tools into Anthropic tool-use format.

    Each tool becomes a tool spec where the LLM can call it by name.
    The input schema is minimal: just tool_name selection since Fleet is fixed.
    """
    tools = available_tools or list(TOOLS.values())
    specs: list[dict[str, Any]] = []

    for tool in tools:
        spec: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": f"Run the {tool.name} analysis tool",
                        "const": tool.name,
                    },
                },
                "required": ["tool_name"],
            },
        }
        specs.append(spec)

    # Add a "done" tool for the orchestrator to signal completion
    specs.append({
        "name": "analysis_complete",
        "description": "Signal that analysis is complete and no more tools need to be run",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was analyzed",
                },
            },
            "required": ["summary"],
        },
    })

    return specs
