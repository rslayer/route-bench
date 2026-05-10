"""Chart visualizations using Altair — returns inline SVG strings."""

from __future__ import annotations

from typing import Any

import altair as alt


def _render_svg(chart: alt.TopLevelMixin) -> str:
    """Render chart to SVG via vl-convert."""
    try:
        import vl_convert as vlc

        vl_spec = chart.to_dict()
        svg_bytes: str = vlc.vegalite_to_svg(vl_spec)
        return svg_bytes
    except Exception:
        return "<svg><text>Chart rendering unavailable</text></svg>"


def _get_val(rm: object, key: str, default: object = None) -> object:
    """Get a value from dict or object attribute."""
    if isinstance(rm, dict):
        return rm.get(key, default)
    return getattr(rm, key, default)


def sequencing_index_distribution(
    route_metrics: dict[str, Any],
) -> str:
    """Distribution of sequencing index across routes."""
    data = []
    for rid, rm in route_metrics.items():
        si = _get_val(rm, "sequencing_index")
        if si is not None:
            data.append({"route_id": rid, "sequencing_index": si})

    if not data:
        return ""

    chart = (
        alt.Chart(alt.Data(values=data))  # type: ignore[no-untyped-call]
        .mark_bar(
            color="#2563eb",
            cornerRadiusTopLeft=3,
            cornerRadiusTopRight=3,
        )
        .encode(
            x=alt.X("route_id:N", sort="-y", title="Route"),
            y=alt.Y("sequencing_index:Q", title="Sequencing Index"),
        )
        .properties(
            width=500, height=250,
            title="Sequencing Index by Route",
        )
    )
    return _render_svg(chart)


def time_distribution(
    route_metrics: dict[str, Any],
    shift_cap_hours: float = 12.0,
) -> str:
    """Distribution of total time across routes with shift cap line."""
    data = []
    for rid, rm in route_metrics.items():
        total = _get_val(rm, "total_time_hours")
        if total is not None:
            data.append({"route_id": rid, "total_time_hours": total})

    if not data:
        return ""

    bars = (
        alt.Chart(alt.Data(values=data))  # type: ignore[no-untyped-call]
        .mark_bar(
            color="#6366f1",
            cornerRadiusTopLeft=3,
            cornerRadiusTopRight=3,
        )
        .encode(
            x=alt.X("route_id:N", sort="-y", title="Route"),
            y=alt.Y("total_time_hours:Q", title="Total Time (hours)"),
        )
    )

    cap_data = [{"shift_cap": shift_cap_hours}]
    rule = (
        alt.Chart(alt.Data(values=cap_data))  # type: ignore[no-untyped-call]
        .mark_rule(color="#dc2626", strokeDash=[5, 3], strokeWidth=2)
        .encode(y=alt.Y("shift_cap:Q"))
    )

    chart = (bars + rule).properties(
        width=500, height=250, title="Total Time by Route",
    )
    return _render_svg(chart)


def capacity_utilization_chart(
    route_metrics: dict[str, Any],
) -> str:
    """Capacity utilization stacked bar chart per route per dimension."""
    data = []
    for rid, rm in route_metrics.items():
        cap = _get_val(rm, "capacity_utilization", {})
        if isinstance(cap, dict):
            for dim, val in cap.items():
                data.append({
                    "route_id": rid,
                    "dimension": dim,
                    "utilization": val * 100,
                })

    if not data:
        return ""

    chart = (
        alt.Chart(alt.Data(values=data))  # type: ignore[no-untyped-call]
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("route_id:N", title="Route"),
            y=alt.Y("utilization:Q", title="Utilization (%)"),
            color=alt.Color("dimension:N", title="Dimension"),
        )
        .properties(
            width=500, height=250,
            title="Capacity Utilization by Route",
        )
    )
    return _render_svg(chart)


def benchmark_gap_chart(
    benchmark_data: dict[str, Any] | None,
) -> str:
    """Per-route benchmark gap bar chart."""
    if not benchmark_data:
        return ""

    per_route = benchmark_data.get("per_route", {})
    data = []
    for rid, rb in per_route.items():
        gap = _get_val(rb, "distance_gap_pct")
        if gap is not None:
            data.append({"route_id": rid, "distance_gap_pct": gap})

    if not data:
        return ""

    chart = (
        alt.Chart(alt.Data(values=data))  # type: ignore[no-untyped-call]
        .mark_bar(
            color="#d97706",
            cornerRadiusTopLeft=3,
            cornerRadiusTopRight=3,
        )
        .encode(
            x=alt.X("route_id:N", sort="-y", title="Route"),
            y=alt.Y("distance_gap_pct:Q", title="Distance Gap (%)"),
        )
        .properties(
            width=500, height=250,
            title="Benchmark Distance Gap by Route",
        )
    )
    return _render_svg(chart)
