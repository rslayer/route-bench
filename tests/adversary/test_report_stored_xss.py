"""Defect: stored XSS in the rendered HTML report. `ReportDocument` builds
its Jinja2 `Environment` with `autoescape=False`, and `base.html.j2`
interpolates `rm.route_id` (and other user-controlled strings) directly:
`<td>{{ rm.route_id }}</td>`.

`route_id` is taken verbatim from the uploaded CSV — `validate_csv` places
no restriction on its character set (control chars, HTML, `<script>` tags
all pass through untouched; see category 7 of the robustness report). The
resulting `report.html` is served by `GET /sessions/{id}/report.html` with
`media_type="text/html"` (see `download_report_html` in
`app/api/routes.py`), so a browser renders it directly.

Impact: an attacker who can get anyone to view a session's report — a
support workflow, a shared link, an internal dashboard that embeds the
report — can plant arbitrary JavaScript that executes in that viewer's
browser, in the RouteBench origin. That is enough to read session data,
forge further requests, or pivot to an admin viewing an uploaded report.

Root cause: `jinja2.Environment(..., autoescape=False)` in
`report/document.py`. Autoescape should be on for an HTML template that
renders user-controlled content; nothing about this document is
intentionally raw HTML from a trusted source.
"""

from __future__ import annotations

from datetime import UTC, datetime

from routebench.core.findings import AnalysisReport, FleetMetrics, RouteMetrics
from routebench.core.schemas import Fleet, Route, Stop
from routebench.report.document import ReportDocument

_PAYLOAD = "<script>alert(document.cookie)</script>"


def _report_with_malicious_route_id(route_id: str) -> AnalysisReport:
    stop = Stop(route_id=route_id, stop_sequence=1, latitude=32.83, longitude=-96.77)
    route = Route(
        route_id=route_id,
        stops=[stop],
        depot_lat=32.8,
        depot_lon=-96.7,
        planned_start_time=datetime.now(UTC),
    )
    fleet = Fleet(routes=[route], upload_id="u1", uploaded_at=datetime.now(UTC))

    fleet_metrics = FleetMetrics(
        total_routes=1,
        total_stops=1,
        total_distance_miles=1.0,
        total_time_hours=1.0,
        routes_over_shift_cap=0,
    )
    route_metrics = {
        route_id: RouteMetrics(
            route_id=route_id,
            total_distance_miles=1.0,
            total_time_hours=1.0,
            drive_time_hours=1.0,
            service_time_hours=0.0,
            idle_time_hours=0.0,
            stop_count=1,
            stops_per_hour=1.0,
        )
    }

    return AnalysisReport(
        fleet=fleet,
        fleet_metrics=fleet_metrics,
        route_metrics=route_metrics,
        findings=[],
        analyses_run=[],
        analyses_skipped=[],
        metadata={},
    )


def test_route_id_is_html_escaped_in_the_report() -> None:
    """A route_id containing a <script> tag must never appear unescaped in
    the rendered report — the report is served as text/html to a browser."""
    analysis = _report_with_malicious_route_id(_PAYLOAD)
    doc = ReportDocument(analysis)

    html = doc.render(
        prose={"fleet_overview_narrative": "x", "investigation_priorities": "y"}
    )

    assert _PAYLOAD not in html, (
        "an unescaped <script> tag from the uploaded route_id appears verbatim "
        "in report.html, which is served with media_type=text/html — this is a "
        "stored XSS"
    )
