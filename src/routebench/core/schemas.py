"""Core schemas for RouteBench.

Defines Stop, Route, Fleet, and ValidationReport types.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator

StopType = Literal["depot", "delivery", "pickup"]


class Stop(BaseModel):
    """A single stop within a route."""

    route_id: str
    stop_sequence: int = Field(ge=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    stop_type: StopType = "delivery"
    planned_arrival_time: datetime | None = None
    service_time_minutes: float = Field(default=5.0, ge=0)
    time_window_start: time | None = None
    time_window_end: time | None = None
    demand_units: float | None = Field(default=None, ge=0)
    demand_weight: float | None = Field(default=None, ge=0)
    demand_volume: float | None = Field(default=None, ge=0)
    customer_id: str | None = None
    address: str | None = None


class Route(BaseModel):
    """A route consisting of ordered stops and a depot."""

    route_id: str
    stops: list[Stop]
    depot_lat: float
    depot_lon: float
    planned_start_time: datetime
    vehicle_capacity_units: float | None = None
    vehicle_capacity_weight: float | None = None
    vehicle_capacity_volume: float | None = None

    @field_validator("stops")
    @classmethod
    def stops_must_be_contiguous(cls, v: list[Stop]) -> list[Stop]:
        """stop_sequence must start at 1 and be contiguous."""
        if not v:
            return v
        sequences = [s.stop_sequence for s in v]
        if sequences[0] != 1:
            msg = f"First stop_sequence must be 1, got {sequences[0]}"
            raise ValueError(msg)
        for i in range(1, len(sequences)):
            if sequences[i] != sequences[i - 1] + 1:
                msg = (
                    f"stop_sequence must be contiguous: "
                    f"expected {sequences[i - 1] + 1}, got {sequences[i]}"
                )
                raise ValueError(msg)
        return v


class Fleet(BaseModel):
    """A collection of routes from a single upload."""

    routes: list[Route]
    upload_id: str
    uploaded_at: datetime

    @field_validator("routes")
    @classmethod
    def at_most_50_routes(cls, v: list[Route]) -> list[Route]:
        """Fleet must have at most 50 routes."""
        if len(v) > 50:
            msg = f"Fleet must have at most 50 routes, got {len(v)}"
            raise ValueError(msg)
        return v

    def total_stops(self) -> int:
        """Total number of stops across all routes."""
        return sum(len(r.stops) for r in self.routes)


class ValidationError(BaseModel):
    """A validation error encountered during CSV parsing."""

    row: int | None
    column: str | None
    code: str
    message: str


class ValidationWarning(BaseModel):
    """A validation warning encountered during CSV parsing."""

    row: int | None
    column: str | None
    code: str
    message: str


class DefaultApplied(BaseModel):
    """Record of a default value applied during validation."""

    field: str
    default_value: str
    affected_row_count: int


class ValidationReport(BaseModel):
    """Result of CSV validation, containing errors, warnings, and defaults applied."""

    is_valid: bool
    errors: list[ValidationError]
    warnings: list[ValidationWarning]
    defaults_applied: list[DefaultApplied]
    summary: dict[str, object]
