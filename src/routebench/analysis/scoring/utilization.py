"""Utilization scoring: capacity usage per dimension."""

from __future__ import annotations

from routebench.core.schemas import Route


def compute_utilization_metrics(route: Route) -> dict[str, object]:
    """Compute utilization metrics for a single route.

    For each capacity dimension where both demand and capacity are present,
    compute used/available. Skip dimensions where data is missing.

    Returns dict keyed by dimension name with utilization ratios.
    """
    utilization: dict[str, float] = {}

    # Units dimension
    if route.vehicle_capacity_units is not None and route.vehicle_capacity_units > 0:
        total_demand = sum(s.demand_units for s in route.stops if s.demand_units is not None)
        if total_demand > 0:
            utilization["units"] = total_demand / route.vehicle_capacity_units

    # Weight dimension
    if route.vehicle_capacity_weight is not None and route.vehicle_capacity_weight > 0:
        total_demand = sum(s.demand_weight for s in route.stops if s.demand_weight is not None)
        if total_demand > 0:
            utilization["weight"] = total_demand / route.vehicle_capacity_weight

    # Volume dimension
    if route.vehicle_capacity_volume is not None and route.vehicle_capacity_volume > 0:
        total_demand = sum(s.demand_volume for s in route.stops if s.demand_volume is not None)
        if total_demand > 0:
            utilization["volume"] = total_demand / route.vehicle_capacity_volume

    return {"capacity_utilization": utilization}
