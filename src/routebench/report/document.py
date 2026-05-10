"""AnalysisReport to HTML rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2

from routebench.analysis.visuals.charts import (
    benchmark_gap_chart,
    capacity_utilization_chart,
    sequencing_index_distribution,
    time_distribution,
)
from routebench.core.findings import AnalysisReport
from routebench.report.prose_slots import ProseSlot, identify_prose_slots

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


class ReportDocument:
    """Renders an AnalysisReport + prose into a standalone HTML file."""

    def __init__(self, analysis: AnalysisReport) -> None:
        self._analysis = analysis
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
            undefined=jinja2.Undefined,
        )

    def identify_prose_slots(self) -> list[ProseSlot]:
        """Identify which prose slots need to be filled."""
        return identify_prose_slots(self._analysis)

    def render(self, prose: dict[str, str]) -> str:
        """Render the full report as a self-contained HTML string.

        Args:
            prose: Dict mapping slot_id to generated prose text.

        Returns:
            Complete HTML document as a string.
        """
        css = (_STATIC_DIR / "styles.css").read_text()

        # Build chart SVGs
        route_metrics_dict: dict[str, Any] = {
            rid: rm.model_dump()
            for rid, rm in self._analysis.route_metrics.items()
        }
        benchmark_dict: dict[str, Any] | None = (
            self._analysis.benchmark.model_dump()
            if self._analysis.benchmark else None
        )

        charts = {
            "time_distribution": time_distribution(route_metrics_dict),
            "sequencing_index": sequencing_index_distribution(route_metrics_dict),
            "capacity_utilization": capacity_utilization_chart(route_metrics_dict),
            "benchmark_gap": benchmark_gap_chart(benchmark_dict),
        }

        # Sort findings by severity for template
        severity_order = {
            "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
        }
        sorted_findings = sorted(
            self._analysis.findings,
            key=lambda f: severity_order.get(f.severity, 4),
        )

        template = self._env.get_template("base.html.j2")
        html = template.render(
            fleet_metrics=self._analysis.fleet_metrics,
            route_metrics=self._analysis.route_metrics,
            findings=sorted_findings,
            benchmark=self._analysis.benchmark,
            prose=prose,
            charts=charts,
            maps={},
            analyses_run=self._analysis.analyses_run,
            analyses_skipped=self._analysis.analyses_skipped,
            metadata=self._analysis.metadata,
            css=css,
        )
        return html
