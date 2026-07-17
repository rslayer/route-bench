"""CSV validation and Fleet construction.

Implements validate_csv: CSV path -> (Fleet | None, ValidationReport).
Uses Polars for CSV reading. Applies validation rules and default filling.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, time
from pathlib import Path

import polars as pl
import structlog
from pydantic import ValidationError as PydanticValidationError

from routebench.core.config import AnalysisConfig
from routebench.core.schemas import (
    DefaultApplied,
    Fleet,
    Route,
    Stop,
    ValidationError,
    ValidationReport,
    ValidationWarning,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

REQUIRED_COLUMNS = {"route_id", "stop_sequence", "latitude", "longitude"}
OPTIONAL_COLUMNS = {
    "stop_type",
    "planned_arrival_time",
    "service_time_minutes",
    "time_window_start",
    "time_window_end",
    "demand_units",
    "demand_weight",
    "demand_volume",
    "customer_id",
    "address",
    "planned_start_time",
    "vehicle_capacity_units",
    "vehicle_capacity_weight",
    "vehicle_capacity_volume",
}

EARTH_RADIUS_MILES = 3958.8


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two points in miles."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_MILES * c


def validate_csv(
    path: Path, config: AnalysisConfig | None = None
) -> tuple[Fleet | None, ValidationReport]:
    """Validate a CSV file and construct a Fleet.

    Returns (Fleet, report) if valid, or (None, report) if errors were found.
    """
    if config is None:
        config = AnalysisConfig()

    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    defaults_applied: list[DefaultApplied] = []

    try:
        df = pl.read_csv(path, infer_schema_length=10000)
    except Exception as exc:
        errors.append(
            ValidationError(
                row=None,
                column=None,
                code="CSV_READ_ERROR",
                message=f"Could not read CSV: {exc}",
            )
        )
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": 0},
        )

    # Normalize column names to lowercase
    df = df.rename({col: col.strip().lower() for col in df.columns})

    # Check required columns
    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        for col in sorted(missing_cols):
            errors.append(
                ValidationError(
                    row=None,
                    column=col,
                    code="MISSING_REQUIRED_COLUMN",
                    message=f"Required column '{col}' is missing",
                )
            )
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df)},
        )

    # Reject a fractional stop_sequence before casting. `cast(pl.Int64)` on a
    # float column truncates silently rather than raising, so "0.9" became 0 —
    # and 0 is what marks the depot. A typo'd sequence quietly promoted a
    # delivery to the depot and reordered the route, with no error anywhere.
    if df["stop_sequence"].dtype in (pl.Float32, pl.Float64):
        non_integral = df.filter(
            pl.col("stop_sequence").is_not_null()
            & (pl.col("stop_sequence") != pl.col("stop_sequence").floor())
        )
        if non_integral.height > 0:
            errors.append(
                ValidationError(
                    row=None,
                    column="stop_sequence",
                    code="INVALID_TYPE",
                    message=(
                        f"stop_sequence must be a whole number; found "
                        f"{non_integral.height} fractional value(s), e.g. "
                        f"{non_integral['stop_sequence'][0]}"
                    ),
                )
            )
            return None, ValidationReport(
                is_valid=False,
                errors=errors,
                warnings=warnings,
                defaults_applied=defaults_applied,
                summary={"total_rows": len(df)},
            )

    # Cast stop_sequence to int
    try:
        df = df.with_columns(pl.col("stop_sequence").cast(pl.Int64))
    except Exception:
        errors.append(
            ValidationError(
                row=None,
                column="stop_sequence",
                code="INVALID_TYPE",
                message="stop_sequence must be integer",
            )
        )
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df)},
        )

    # Cast lat/lon to float
    for col in ["latitude", "longitude"]:
        try:
            df = df.with_columns(pl.col(col).cast(pl.Float64))
        except Exception:
            errors.append(
                ValidationError(
                    row=None,
                    column=col,
                    code="INVALID_TYPE",
                    message=f"{col} must be numeric",
                )
            )

    if errors:
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df)},
        )

    # Per-row validation
    for row_idx in range(len(df)):
        row = df.row(row_idx, named=True)

        # Check nulls in required fields
        for col in REQUIRED_COLUMNS:
            if row[col] is None:
                errors.append(
                    ValidationError(
                        row=row_idx + 1,
                        column=col,
                        code="NULL_REQUIRED_FIELD",
                        message=f"Required field '{col}' is null at row {row_idx + 1}",
                    )
                )

        # Lat/lon range validation.
        #
        # `not (-90 <= lat <= 90)` rather than `lat < -90 or lat > 90`: NaN
        # compares False against EVERY bound, so the second form lets NaN
        # through as if it were in range. That is not theoretical — a NaN on a
        # depot row was accepted silently (is_valid=True) and carried into
        # routing, distance maths, and the geojson export. Inverting the
        # in-range test makes NaN fail, since "is it inside?" is False for NaN.
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is not None and not (-90 <= lat <= 90):
            errors.append(
                ValidationError(
                    row=row_idx + 1,
                    column="latitude",
                    code="OUT_OF_RANGE",
                    message=f"latitude {lat} out of range [-90, 90] at row {row_idx + 1}",
                )
            )
        if lon is not None and not (-180 <= lon <= 180):
            errors.append(
                ValidationError(
                    row=row_idx + 1,
                    column="longitude",
                    code="OUT_OF_RANGE",
                    message=f"longitude {lon} out of range [-180, 180] at row {row_idx + 1}",
                )
            )
        # Flag (0, 0) as suspicious
        if lat is not None and lon is not None and lat == 0.0 and lon == 0.0:
            errors.append(
                ValidationError(
                    row=row_idx + 1,
                    column="latitude",
                    code="ZERO_COORDINATES",
                    message=f"Coordinates (0, 0) at row {row_idx + 1} are likely invalid",
                )
            )

    if errors:
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df)},
        )

    # Apply defaults for optional fields
    service_time_default = config.service_time.default_minutes

    # service_time_minutes default
    if "service_time_minutes" in df.columns:
        null_count = df.filter(pl.col("service_time_minutes").is_null()).height
        if null_count > 0:
            df = df.with_columns(pl.col("service_time_minutes").fill_null(service_time_default))
            defaults_applied.append(
                DefaultApplied(
                    field="service_time_minutes",
                    default_value=str(service_time_default),
                    affected_row_count=null_count,
                )
            )
    else:
        df = df.with_columns(pl.lit(service_time_default).alias("service_time_minutes"))
        defaults_applied.append(
            DefaultApplied(
                field="service_time_minutes",
                default_value=str(service_time_default),
                affected_row_count=len(df),
            )
        )

    # stop_type default: "depot" if stop_sequence==0, else "delivery"
    if "stop_type" in df.columns:
        null_count = df.filter(pl.col("stop_type").is_null()).height
        if null_count > 0:
            df = df.with_columns(
                pl.when(pl.col("stop_type").is_null() & (pl.col("stop_sequence") == 0))
                .then(pl.lit("depot"))
                .when(pl.col("stop_type").is_null())
                .then(pl.lit("delivery"))
                .otherwise(pl.col("stop_type"))
                .alias("stop_type")
            )
            defaults_applied.append(
                DefaultApplied(
                    field="stop_type",
                    default_value="delivery (or depot if stop_sequence=0)",
                    affected_row_count=null_count,
                )
            )
    else:
        df = df.with_columns(
            pl.when(pl.col("stop_sequence") == 0)
            .then(pl.lit("depot"))
            .otherwise(pl.lit("delivery"))
            .alias("stop_type")
        )
        defaults_applied.append(
            DefaultApplied(
                field="stop_type",
                default_value="delivery (or depot if stop_sequence=0)",
                affected_row_count=len(df),
            )
        )

    # planned_start_time default: 8:00 AM today
    default_start = datetime.now(tz=UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    if "planned_start_time" not in df.columns:
        df = df.with_columns(pl.lit(default_start.isoformat()).alias("planned_start_time"))
        defaults_applied.append(
            DefaultApplied(
                field="planned_start_time",
                default_value="08:00 AM (upload date)",
                affected_row_count=len(df),
            )
        )

    # Check for duplicate (route_id, stop_sequence)
    dup_check = df.group_by(["route_id", "stop_sequence"]).len()
    dups = dup_check.filter(pl.col("len") > 1)
    if len(dups) > 0:
        for dup_row in dups.iter_rows(named=True):
            errors.append(
                ValidationError(
                    row=None,
                    column=None,
                    code="DUPLICATE_STOP",
                    message=(
                        f"Duplicate (route_id={dup_row['route_id']}, "
                        f"stop_sequence={dup_row['stop_sequence']})"
                    ),
                )
            )
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df)},
        )

    # Group by route and build Fleet
    route_groups = df.partition_by("route_id", as_dict=True, maintain_order=True)

    if len(route_groups) > 50:
        errors.append(
            ValidationError(
                row=None,
                column=None,
                code="TOO_MANY_ROUTES",
                message=f"Fleet has {len(route_groups)} routes, max is 50",
            )
        )
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df)},
        )

    total_stops = 0
    routes: list[Route] = []

    for route_id_key, route_df in route_groups.items():
        route_id = route_id_key[0] if isinstance(route_id_key, tuple) else route_id_key
        route_id = str(route_id)
        route_df = route_df.sort("stop_sequence")

        sequences = route_df["stop_sequence"].to_list()

        # Check depot: stop_sequence=0 is the depot
        has_depot = sequences[0] == 0
        if not has_depot:
            errors.append(
                ValidationError(
                    row=None,
                    column="stop_sequence",
                    code="MISSING_DEPOT",
                    message=f"Route '{route_id}' has no depot row (stop_sequence=0)",
                )
            )
            continue

        # Validate contiguity: 0, 1, 2, 3, ...
        expected = list(range(len(sequences)))
        if sequences != expected:
            errors.append(
                ValidationError(
                    row=None,
                    column="stop_sequence",
                    code="NON_CONTIGUOUS_SEQUENCE",
                    message=(f"Route '{route_id}' has non-contiguous stop_sequence: {sequences}"),
                )
            )
            continue

        # Extract depot row
        depot_row = route_df.row(0, named=True)
        depot_lat = float(depot_row["latitude"])
        depot_lon = float(depot_row["longitude"])

        # Get planned_start_time
        pst_raw = depot_row.get("planned_start_time")
        if pst_raw is not None and isinstance(pst_raw, str):
            try:
                planned_start = datetime.fromisoformat(pst_raw)
            except ValueError:
                planned_start = default_start
        elif isinstance(pst_raw, datetime):
            planned_start = pst_raw
        else:
            planned_start = default_start

        # Get vehicle capacities from depot row
        vehicle_capacity_units = _safe_float(depot_row.get("vehicle_capacity_units"))
        vehicle_capacity_weight = _safe_float(depot_row.get("vehicle_capacity_weight"))
        vehicle_capacity_volume = _safe_float(depot_row.get("vehicle_capacity_volume"))

        # Build stops (sequence 1+)
        stop_rows = route_df.slice(1)
        stops: list[Stop] = []

        for i in range(stop_rows.height):
            srow = stop_rows.row(i, named=True)
            # Stop's own field constraints (service_time_minutes >= 0, and the
            # rest) raise pydantic's ValidationError, which is a different class
            # from this module's ValidationError and so was never caught here.
            # validate_csv is called straight from the POST /sessions handler,
            # so a single negative service time in a CSV surfaced as a 500
            # instead of the 422 every other bad cell gets.
            try:
                stop = Stop(
                    route_id=route_id,
                    stop_sequence=int(srow["stop_sequence"]),
                    latitude=float(srow["latitude"]),
                    longitude=float(srow["longitude"]),
                    stop_type=srow.get("stop_type", "delivery") or "delivery",
                    planned_arrival_time=_parse_datetime(srow.get("planned_arrival_time")),
                    service_time_minutes=float(srow.get("service_time_minutes", 5.0) or 5.0),
                    time_window_start=_parse_time(srow.get("time_window_start")),
                    time_window_end=_parse_time(srow.get("time_window_end")),
                    demand_units=_safe_float(srow.get("demand_units")),
                    demand_weight=_safe_float(srow.get("demand_weight")),
                    demand_volume=_safe_float(srow.get("demand_volume")),
                    customer_id=_safe_str(srow.get("customer_id")),
                    address=_safe_str(srow.get("address")),
                )
            except PydanticValidationError as exc:
                for err in exc.errors():
                    field = ".".join(str(p) for p in err["loc"]) or "(row)"
                    errors.append(
                        ValidationError(
                            row=None,
                            column=field,
                            code="INVALID_VALUE",
                            message=f"Route '{route_id}', stop {srow['stop_sequence']}: {err['msg']}",
                        )
                    )
                continue
            stops.append(stop)

        total_stops += len(stops)

        # Bounding box check: flag if any pair of stops >500 miles apart
        all_coords = [(depot_lat, depot_lon)] + [(s.latitude, s.longitude) for s in stops]
        _check_bounding_box(route_id, all_coords, warnings)

        route = Route(
            route_id=route_id,
            stops=stops,
            depot_lat=depot_lat,
            depot_lon=depot_lon,
            planned_start_time=planned_start,
            vehicle_capacity_units=vehicle_capacity_units,
            vehicle_capacity_weight=vehicle_capacity_weight,
            vehicle_capacity_volume=vehicle_capacity_volume,
        )
        routes.append(route)

    if errors:
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df), "route_count": len(routes)},
        )

    # Check total stops limit
    if total_stops > 5000:
        errors.append(
            ValidationError(
                row=None,
                column=None,
                code="TOO_MANY_STOPS",
                message=f"Fleet has {total_stops} total stops, max is 5,000",
            )
        )
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df), "route_count": len(routes)},
        )

    # Any error accumulated while building rows (a stop that failed its own
    # field constraints, say) means we do not have the fleet the caller
    # uploaded — some stops were skipped. Returning it anyway with
    # is_valid=True, as this did, would analyse a quietly truncated route and
    # report the result as if it were the whole thing.
    if errors:
        return None, ValidationReport(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            defaults_applied=defaults_applied,
            summary={"total_rows": len(df), "route_count": len(routes)},
        )

    upload_id = str(uuid.uuid4())
    fleet = Fleet(
        routes=routes,
        upload_id=upload_id,
        uploaded_at=datetime.now(tz=UTC),
    )

    logger.info(
        "csv_validated",
        upload_id=upload_id,
        route_count=len(routes),
        total_stops=total_stops,
    )

    return fleet, ValidationReport(
        is_valid=True,
        errors=errors,
        warnings=warnings,
        defaults_applied=defaults_applied,
        summary={
            "total_rows": len(df),
            "route_count": len(routes),
            "total_stops": total_stops,
        },
    )


def _check_bounding_box(
    route_id: str,
    coords: list[tuple[float, float]],
    warnings: list[ValidationWarning],
) -> None:
    """Flag if a route's stops span more than 500 miles corner to corner.

    Measures the route's bounding box — the name's promise — in a single pass.
    The previous implementation compared every pair of stops, which is O(n^2)
    and runs synchronously inside POST /sessions: a single well-formed route
    with a few thousand clustered stops, comfortably inside the 5,000-stop
    limit, burned seconds of CPU in the request handler and needed no malice to
    trigger.

    The box diagonal is an upper bound on the distance between any two stops in
    it, so a route that stays inside 500 miles still never warns. A route that
    trips this is flagged on its span rather than on a specific pair; the check
    exists to catch a typo'd coordinate, and one bad digit blows out the box.
    """
    if len(coords) < 2:
        return

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    span = _haversine_miles(min_lat, min_lon, max_lat, max_lon)
    if span > 500:
        warnings.append(
            ValidationWarning(
                row=None,
                column=None,
                code="LARGE_BOUNDING_BOX",
                message=(
                    f"Route '{route_id}': stops span {span:.0f} miles corner to corner "
                    f"(>500 miles, possible typo)"
                ),
            )
        )


def _safe_float(val: object) -> float | None:
    """Safely convert a value to float, returning None if not possible."""
    if val is None:
        return None
    try:
        result = float(val)  # type: ignore[arg-type]
        if math.isnan(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def _safe_str(val: object) -> str | None:
    """Safely convert a value to string, returning None if empty."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _parse_datetime(val: object) -> datetime | None:
    """Parse a datetime value from various formats."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        val_stripped = val.strip()
        if not val_stripped:
            return None
        try:
            return datetime.fromisoformat(val_stripped)
        except ValueError:
            return None
    return None


def _parse_time(val: object) -> time | None:
    """Parse a time value from various formats."""
    if val is None:
        return None
    if isinstance(val, time):
        return val
    if isinstance(val, str):
        val_stripped = val.strip()
        if not val_stripped:
            return None
        try:
            return time.fromisoformat(val_stripped)
        except ValueError:
            return None
    return None
