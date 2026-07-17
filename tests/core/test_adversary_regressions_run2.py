"""Regression guards for the defects found by robustness run 2.

Each test here failed against the code as it stood at the run. They are the
promoted form of tests/adversary/*.py from that run: rewritten to assert the
behaviour we want rather than to document the behaviour we had, and moved into
the CI suite so the defect cannot come back quietly.

See ROBUSTNESS.md for the run history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from routebench.core.config import AnalysisConfig
from routebench.core.findings import AnalysisReport, FleetMetrics, RouteMetrics
from routebench.core.schemas import Fleet, Route, Stop
from routebench.core.validation import validate_csv

_EVIL = "<script>alert(1)</script>"


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "routes.csv"
    p.write_text(body)
    return p


def _make_report_with_route_id(route_id: str) -> AnalysisReport:
    """A minimal one-route report whose route_id is caller-controlled — the
    shape an uploaded CSV produces."""
    route = Route(
        route_id=route_id,
        depot_lat=32.7767,
        depot_lon=-96.7970,
        planned_start_time=datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
        stops=[
            Stop(
                route_id=route_id,
                stop_sequence=i,
                latitude=32.78 + i * 0.01,
                longitude=-96.80 - i * 0.01,
            )
            for i in (1, 2)
        ],
    )
    fleet = Fleet(
        routes=[route],
        upload_id="xss-test",
        uploaded_at=datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
    )
    return AnalysisReport(
        fleet=fleet,
        fleet_metrics=FleetMetrics(
            total_routes=1,
            total_stops=2,
            total_distance_miles=10.0,
            total_time_hours=1.0,
            routes_over_shift_cap=0,
        ),
        route_metrics={
            route_id: RouteMetrics(
                route_id=route_id,
                stop_count=2,
                total_distance_miles=10.0,
                total_time_hours=1.0,
                drive_time_hours=0.8,
                service_time_hours=0.2,
                idle_time_hours=0.0,
                stops_per_hour=2.0,
            )
        },
        findings=[],
        analyses_run=[],
        analyses_skipped=[],
        metadata={"session_id": "xss-test"},
    )


def _prose_for(report: AnalysisReport) -> dict[str, str]:
    return {slot.slot_id: "Prose." for slot in report_slots(report)}


def report_slots(report: AnalysisReport):
    from routebench.report.document import ReportDocument

    return ReportDocument(report).identify_prose_slots()


class TestStoredXSSInReport:
    """A route_id is copied verbatim from the upload into report.html, which is
    served as text/html. With Jinja autoescape off it executed in the viewer's
    browser."""

    def test_script_tag_in_route_id_is_escaped_in_rendered_html(self) -> None:
        """Renders the real report with a payload as the route_id, the way an
        upload would carry it, and asserts the browser cannot execute it."""
        from routebench.report.document import ReportDocument

        report = _make_report_with_route_id(_EVIL)
        doc = ReportDocument(report)
        html = doc.render(_prose_for(report))

        assert _EVIL not in html, (
            "route_id reached report.html unescaped — stored XSS: the report is "
            "served as text/html by GET /sessions/{id}/report.html."
        )
        assert "&lt;script&gt;" in html, "expected the payload to survive as escaped text"

    def test_autoescape_is_on(self) -> None:
        """Belt and braces: the rendered-output test above only covers the
        templates that exist today."""
        from routebench.report.document import ReportDocument

        doc = ReportDocument(_make_report_with_route_id("R-001"))
        assert doc._env.autoescape is True

    def test_chart_svg_still_renders_unescaped(self) -> None:
        """The fix must not escape the chart SVG into visible angle brackets."""
        from routebench.analysis.visuals.charts import sequencing_index_distribution

        svg = sequencing_index_distribution({"R-001": {"sequencing_index": 1.4}})
        assert svg.lstrip().startswith("<svg") or "<svg" in svg


class TestTrafficSpeedFactorBounds:
    """`Field(gt=0)` admits inf, because `inf > 0` is True."""

    def test_overflowing_default_factor_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AnalysisConfig(traffic={"default_factor": 1e400})

    def test_overflowing_band_speed_factor_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AnalysisConfig(
                traffic={
                    "bands": [
                        {"start": "07:00", "end": "09:00", "speed_factor": 1e400},
                    ]
                }
            )

    def test_nan_factor_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AnalysisConfig(traffic={"default_factor": float("nan")})

    def test_sane_factor_still_accepted(self) -> None:
        cfg = AnalysisConfig(traffic={"default_factor": 0.8})
        assert cfg.traffic.default_factor == 0.8


class TestUnknownConfigFieldRejected:
    """A misspelled key silently fell back to the default, and the caller got a
    202 for an analysis that ignored the constraint they set."""

    def test_misspelled_nested_key_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AnalysisConfig(work_rules={"mx_shift_hours": 8})

    def test_unknown_top_level_key_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AnalysisConfig(not_a_real_setting=True)

    def test_correctly_spelled_key_still_applies(self) -> None:
        cfg = AnalysisConfig(work_rules={"max_shift_hours": 8})
        assert cfg.work_rules.max_shift_hours == 8


class TestNegativeServiceTime:
    """Stop's own constraints raise pydantic's ValidationError, a different
    class from this package's — so it escaped validate_csv as a 500."""

    def test_negative_service_time_is_a_validation_error_not_a_crash(self, tmp_path: Path) -> None:
        csv = _write(
            tmp_path,
            "route_id,stop_sequence,latitude,longitude,service_time_minutes\n"
            "R-001,0,32.7767,-96.7970,0\n"
            "R-001,1,32.7800,-96.8000,-5\n",
        )
        fleet, report = validate_csv(csv)
        assert fleet is None
        assert report.is_valid is False
        assert report.errors, "expected a structured validation error, not a crash"


class TestFractionalStopSequence:
    """cast(pl.Int64) truncates a float column silently; 0.9 became 0, and 0 is
    what marks the depot."""

    def test_fractional_stop_sequence_is_rejected(self, tmp_path: Path) -> None:
        csv = _write(
            tmp_path,
            "route_id,stop_sequence,latitude,longitude\n"
            "R-001,0,32.7767,-96.7970\n"
            "R-001,0.9,32.7800,-96.8000\n"
            "R-001,1.9,32.7850,-96.8050\n",
        )
        fleet, report = validate_csv(csv)
        assert fleet is None, "a fractional stop_sequence was silently truncated"
        assert report.is_valid is False
        assert any(e.column == "stop_sequence" for e in report.errors)

    def test_whole_number_stop_sequence_still_accepted(self, tmp_path: Path) -> None:
        csv = _write(
            tmp_path,
            "route_id,stop_sequence,latitude,longitude\n"
            "R-001,0,32.7767,-96.7970\n"
            "R-001,1,32.7800,-96.8000\n",
        )
        fleet, report = validate_csv(csv)
        assert fleet is not None
        assert report.is_valid is True


class TestBoundingBoxIsLinear:
    """The check compared every pair of stops — O(n^2) — synchronously inside
    the POST /sessions handler."""

    def test_bounding_box_work_is_linear_in_stop_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counts distance computations rather than seconds.

        A wall-clock assertion is both flaky on shared CI and too blunt to catch
        this: the quadratic version validated 3,000 stops in ~1.7s locally, so a
        2s budget passed while the defect was live. The call count is exact —
        quadratic is ~n^2/2 haversines per route, linear is one.
        """
        from routebench.core import validation as validation_mod

        calls = 0
        real = validation_mod._haversine_miles

        def counting(*args: float) -> float:
            nonlocal calls
            calls += 1
            return real(*args)

        monkeypatch.setattr(validation_mod, "_haversine_miles", counting)

        n = 400
        rows = ["route_id,stop_sequence,latitude,longitude", "R-001,0,32.7767,-96.7970"]
        rows += [
            f"R-001,{i},{32.7767 + i * 0.00001:.6f},{-96.7970 + i * 0.00001:.6f}"
            for i in range(1, n)
        ]
        csv = _write(tmp_path, "\n".join(rows) + "\n")

        fleet, _report = validate_csv(csv)

        assert fleet is not None
        assert calls < n, (
            f"the bounding box check made {calls} distance computations for {n} "
            f"stops (quadratic would be ~{n * n // 2}); it compares every pair "
            f"again and runs synchronously inside POST /sessions."
        )

    def test_far_apart_stops_still_warn(self, tmp_path: Path) -> None:
        csv = _write(
            tmp_path,
            "route_id,stop_sequence,latitude,longitude\n"
            "R-001,0,32.7767,-96.7970\n"  # Dallas
            "R-001,1,40.7128,-74.0060\n",  # New York — ~1370 miles
        )
        fleet, report = validate_csv(csv)
        assert fleet is not None
        assert any(w.code == "LARGE_BOUNDING_BOX" for w in report.warnings)

    def test_normal_route_does_not_warn(self, tmp_path: Path) -> None:
        csv = _write(
            tmp_path,
            "route_id,stop_sequence,latitude,longitude\n"
            "R-001,0,32.7767,-96.7970\n"
            "R-001,1,32.7800,-96.8000\n",
        )
        _fleet, report = validate_csv(csv)
        assert not any(w.code == "LARGE_BOUNDING_BOX" for w in report.warnings)
