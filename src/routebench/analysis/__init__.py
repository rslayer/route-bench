"""Analysis package — exposes the TOOLS registry with all diagnosis + benchmark tools."""

import routebench.analysis.diagnosis  # noqa: F401 — triggers registration
from routebench.analysis.benchmark import FleetBenchmarkTool, RouteBenchmarkTool
from routebench.analysis.tools import TOOLS, register_tool

register_tool(RouteBenchmarkTool())
register_tool(FleetBenchmarkTool())

__all__ = ["TOOLS"]
