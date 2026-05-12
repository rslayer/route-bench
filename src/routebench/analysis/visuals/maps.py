"""Map visualizations for route findings using staticmap.

Pragmatic v1: uses staticmap (OSM tiles) for inline base64 PNGs.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def render_route_map(
    route_data: dict[str, Any],
    flagged_stops: list[dict[str, Any]] | None = None,
    width: int = 400,
    height: int = 300,
) -> str:
    """Render a route map as a base64-encoded PNG string.

    Args:
        route_data: Dict with depot_lat, depot_lon, and stops list.
        flagged_stops: Optional list of stops to highlight.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Base64-encoded PNG data URI, or empty string on failure.
    """
    try:
        from staticmap import CircleMarker, Line, StaticMap

        m = StaticMap(width, height)

        depot_lat = route_data.get("depot_lat")
        depot_lon = route_data.get("depot_lon")
        stops = route_data.get("stops", [])

        if depot_lat is None or depot_lon is None:
            return ""

        # Add depot marker (red)
        m.add_marker(
            CircleMarker(
                (depot_lon, depot_lat),
                "red",
                8,
            )
        )

        # Build route line
        coords: list[tuple[float, float]] = [(depot_lon, depot_lat)]

        flagged_seqs: set[int] = set()
        if flagged_stops:
            for fs in flagged_stops:
                seq = fs.get("stop_sequence")
                if seq is not None:
                    flagged_seqs.add(seq)

        for stop in stops:
            lat = stop.get("latitude")
            lon = stop.get("longitude")
            if lat is None or lon is None:
                continue

            coords.append((lon, lat))

            seq = stop.get("stop_sequence", 0)
            if seq in flagged_seqs:
                m.add_marker(CircleMarker((lon, lat), "orange", 6))
            else:
                m.add_marker(CircleMarker((lon, lat), "#2563eb", 4))

        # Return to depot
        coords.append((depot_lon, depot_lat))

        if len(coords) > 1:
            m.add_line(Line(coords, "#2563eb", 2))

        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    except Exception:
        logger.exception("map_render_error")
        return ""
