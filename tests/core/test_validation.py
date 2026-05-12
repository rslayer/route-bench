"""Tests for core/validation.py — CSV validation and Fleet construction."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from routebench.core.validation import validate_csv


def _write_csv(rows: list[dict[str, object]], path: Path) -> Path:
    """Helper: write rows to a CSV file."""
    if not rows:
        path.write_text("")
        return path
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _minimal_rows(
    route_id: str = "R001",
    n_stops: int = 3,
    depot_lat: float = 32.825,
    depot_lon: float = -96.775,
) -> list[dict[str, object]]:
    """Generate a minimal valid set of CSV rows with depot + n delivery stops."""
    rows: list[dict[str, object]] = []
    # Depot row
    rows.append({
        "route_id": route_id,
        "stop_sequence": 0,
        "latitude": depot_lat,
        "longitude": depot_lon,
        "stop_type": "depot",
        "service_time_minutes": 0,
        "planned_start_time": "2025-01-15T08:00:00+00:00",
    })
    # Delivery stops
    for i in range(1, n_stops + 1):
        rows.append({
            "route_id": route_id,
            "stop_sequence": i,
            "latitude": depot_lat + 0.01 * i,
            "longitude": depot_lon + 0.01 * i,
            "stop_type": "delivery",
            "service_time_minutes": 5.0,
            "planned_start_time": "2025-01-15T08:00:00+00:00",
        })
    return rows


class TestValidCSV:
    """Tests for valid CSV inputs."""

    def test_valid_minimal_csv(self, tmp_path: Path) -> None:
        """Valid minimal CSV produces a valid Fleet."""
        rows = _minimal_rows()
        csv_path = _write_csv(rows, tmp_path / "valid.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is True
        assert fleet is not None
        assert len(fleet.routes) == 1
        assert fleet.routes[0].route_id == "R001"
        assert len(fleet.routes[0].stops) == 3
        assert fleet.total_stops() == 3
        assert report.errors == []

    def test_valid_multi_route(self, tmp_path: Path) -> None:
        """Multiple routes in one CSV are parsed correctly."""
        rows = _minimal_rows("R001", 2) + _minimal_rows("R002", 4)
        csv_path = _write_csv(rows, tmp_path / "multi.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is True
        assert fleet is not None
        assert len(fleet.routes) == 2
        assert fleet.routes[0].route_id == "R001"
        assert len(fleet.routes[0].stops) == 2
        assert fleet.routes[1].route_id == "R002"
        assert len(fleet.routes[1].stops) == 4

    def test_depot_extracted_correctly(self, tmp_path: Path) -> None:
        """Depot lat/lon extracted from stop_sequence=0 row."""
        rows = _minimal_rows(depot_lat=30.25, depot_lon=-97.75)
        csv_path = _write_csv(rows, tmp_path / "depot.csv")

        fleet, _report = validate_csv(csv_path)

        assert fleet is not None
        route = fleet.routes[0]
        assert route.depot_lat == pytest.approx(30.25)
        assert route.depot_lon == pytest.approx(-97.75)
        # Depot should NOT be in the stops list
        for stop in route.stops:
            assert stop.stop_sequence != 0

    def test_route_stop_count(self, tmp_path: Path) -> None:
        """Fleet.total_stops() returns correct count."""
        rows = _minimal_rows("R001", 5)
        csv_path = _write_csv(rows, tmp_path / "count.csv")

        fleet, _report = validate_csv(csv_path)

        assert fleet is not None
        assert fleet.total_stops() == 5


class TestMissingColumns:
    """Tests for missing required columns."""

    def test_missing_required_column(self, tmp_path: Path) -> None:
        """Missing required column produces an error."""
        rows = [
            {"route_id": "R001", "stop_sequence": 0, "latitude": 32.825},
        ]
        csv_path = _write_csv(rows, tmp_path / "missing_col.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        assert fleet is None
        codes = [e.code for e in report.errors]
        assert "MISSING_REQUIRED_COLUMN" in codes


class TestOutOfRange:
    """Tests for out-of-range values."""

    def test_latitude_out_of_range(self, tmp_path: Path) -> None:
        """Latitude outside [-90, 90] produces an error."""
        rows = _minimal_rows()
        rows[1]["latitude"] = 91.0
        csv_path = _write_csv(rows, tmp_path / "bad_lat.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        assert fleet is None
        codes = [e.code for e in report.errors]
        assert "OUT_OF_RANGE" in codes

    def test_longitude_out_of_range(self, tmp_path: Path) -> None:
        """Longitude outside [-180, 180] produces an error."""
        rows = _minimal_rows()
        rows[1]["longitude"] = -181.0
        csv_path = _write_csv(rows, tmp_path / "bad_lon.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        assert fleet is None
        codes = [e.code for e in report.errors]
        assert "OUT_OF_RANGE" in codes

    def test_zero_coordinates(self, tmp_path: Path) -> None:
        """(0, 0) coordinates flagged as likely invalid."""
        rows = _minimal_rows()
        rows[1]["latitude"] = 0.0
        rows[1]["longitude"] = 0.0
        csv_path = _write_csv(rows, tmp_path / "zero.csv")

        _fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        codes = [e.code for e in report.errors]
        assert "ZERO_COORDINATES" in codes


class TestSequenceValidation:
    """Tests for stop_sequence contiguity."""

    def test_non_contiguous_sequence(self, tmp_path: Path) -> None:
        """Non-contiguous stop_sequence produces an error."""
        rows = [
            {"route_id": "R001", "stop_sequence": 0, "latitude": 32.825, "longitude": -96.775},
            {"route_id": "R001", "stop_sequence": 1, "latitude": 32.835, "longitude": -96.765},
            {"route_id": "R001", "stop_sequence": 3, "latitude": 32.845, "longitude": -96.755},
        ]
        csv_path = _write_csv(rows, tmp_path / "gap.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        assert fleet is None
        codes = [e.code for e in report.errors]
        assert "NON_CONTIGUOUS_SEQUENCE" in codes


class TestDuplicateStops:
    """Tests for duplicate (route_id, stop_sequence) pairs."""

    def test_duplicate_stop(self, tmp_path: Path) -> None:
        """Duplicate (route_id, stop_sequence) produces an error."""
        rows = _minimal_rows()
        # Add a duplicate of stop 1
        rows.append({
            "route_id": "R001",
            "stop_sequence": 1,
            "latitude": 32.835,
            "longitude": -96.765,
            "stop_type": "delivery",
            "service_time_minutes": 5.0,
            "planned_start_time": "2025-01-15T08:00:00+00:00",
        })
        csv_path = _write_csv(rows, tmp_path / "dup.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        assert fleet is None
        codes = [e.code for e in report.errors]
        assert "DUPLICATE_STOP" in codes


class TestTooManyRoutes:
    """Tests for fleet size limits."""

    def test_51_routes_error(self, tmp_path: Path) -> None:
        """51 routes produces an error."""
        rows: list[dict[str, object]] = []
        for i in range(51):
            route_id = f"R{i:03d}"
            rows.append({
                "route_id": route_id,
                "stop_sequence": 0,
                "latitude": 32.825 + 0.001 * i,
                "longitude": -96.775,
            })
            rows.append({
                "route_id": route_id,
                "stop_sequence": 1,
                "latitude": 32.835 + 0.001 * i,
                "longitude": -96.765,
            })
        csv_path = _write_csv(rows, tmp_path / "many.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is False
        assert fleet is None
        codes = [e.code for e in report.errors]
        assert "TOO_MANY_ROUTES" in codes


class TestDefaults:
    """Tests for default value application."""

    def test_missing_optional_fields_defaults(self, tmp_path: Path) -> None:
        """Missing optional fields get defaults with DefaultApplied entries."""
        rows = [
            {"route_id": "R001", "stop_sequence": 0, "latitude": 32.825, "longitude": -96.775},
            {"route_id": "R001", "stop_sequence": 1, "latitude": 32.835, "longitude": -96.765},
            {"route_id": "R001", "stop_sequence": 2, "latitude": 32.845, "longitude": -96.755},
        ]
        csv_path = _write_csv(rows, tmp_path / "defaults.csv")

        fleet, report = validate_csv(csv_path)

        assert report.is_valid is True
        assert fleet is not None
        # Check defaults were applied
        default_fields = [d.field for d in report.defaults_applied]
        assert "service_time_minutes" in default_fields
        assert "stop_type" in default_fields
        assert "planned_start_time" in default_fields


class TestSyntheticRoundTrip:
    """Test that generated synthetic data validates successfully."""

    def test_synthetic_validates(self, tmp_path: Path) -> None:
        """Synthetic CSV from generate_synthetic.py validates to a Fleet."""
        # Import and generate inline
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from generate_synthetic import generate_synthetic

        out = tmp_path / "synthetic.csv"
        generate_synthetic(n_routes=5, avg_stops_per_route=10, output=str(out), seed=42)

        fleet, report = validate_csv(out)

        assert report.is_valid is True
        assert fleet is not None
        assert len(fleet.routes) == 5
        assert fleet.total_stops() == 50
