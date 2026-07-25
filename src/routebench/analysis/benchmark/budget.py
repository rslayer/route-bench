"""Adaptive solver time budgets.

OR-Tools' guided local search spends its whole time limit every run rather than
stopping when it converges, so a fixed budget makes a 5-stop route and a 50-stop
route take exactly as long — and small fleets wait minutes for a solve that
converged in seconds. These scale the budget with problem size instead: a floor
so a tiny problem still gets a fair shot, a per-stop rate, and the configured
limit as the ceiling for the large problems that genuinely need it.

Turned off (adaptive_solver_budget = False) both fall back to the fixed limits,
so the thorough-baseline behaviour is one flag away.
"""

from __future__ import annotations

from routebench.core.config import AnalysisConfig


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def route_time_limit_s(n_stops: int, config: AnalysisConfig) -> int:
    """Per-route TSPTW budget, scaled by that route's stop count."""
    if not config.adaptive_solver_budget:
        return config.route_benchmark_time_limit_s
    scaled = round(config.solver_seconds_per_route_stop * max(0, n_stops))
    return _clamp(scaled, config.route_min_time_limit_s, config.route_benchmark_time_limit_s)


def fleet_time_limit_s(total_stops: int, config: AnalysisConfig) -> int:
    """Fleet VRPTW budget, scaled by the fleet's total stop count."""
    if not config.adaptive_solver_budget:
        return config.fleet_benchmark_time_limit_s
    scaled = round(config.solver_seconds_per_fleet_stop * max(0, total_stops))
    return _clamp(scaled, config.fleet_min_time_limit_s, config.fleet_benchmark_time_limit_s)
