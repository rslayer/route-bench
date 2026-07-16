"""OSRM route geometry — the path a vehicle actually drives.

This is a different OSRM service from the matrix layer. `infra/matrix/osrm.py`
calls `/table` for travel-time and distance matrices; this calls `/route` for the
polyline between stops. The matrix tells you a leg is 12.5 miles; only this tells
you which roads those miles run along.

Degrades rather than fails. If OSRM is unreachable, slow, or refuses the request
(too many waypoints, unroutable coordinates), the caller still gets a drawable
line — straight segments between stops — tagged `approximate` so the UI can say
so. A map that draws the wrong line silently is worse than one that admits the
line is a sketch, and a geometry failure must never fail an analysis that has
already produced real findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

DEFAULT_TIMEOUT_SECONDS = 15.0

# OSRM's own default --max-viaroute-size is 500. Asking beyond it earns a 400,
# which the fallback would handle anyway — but checking first saves a doomed
# round trip and gives a clearer log line.
MAX_WAYPOINTS = 500

GeometryQuality = Literal["exact", "approximate"]

# GeoJSON order: [longitude, latitude].
Position = list[float]


@dataclass(frozen=True)
class RouteGeometry:
    """A drawable line, and an honest account of where it came from.

    `quality` is "exact" only when OSRM returned a real road path. Anything else
    is "approximate": straight segments, correct in order but not in path.
    """

    positions: list[Position]
    quality: GeometryQuality


def straight_line(coords: list[tuple[float, float]]) -> RouteGeometry:
    """Straight segments through the stops, in order. Always available."""
    return RouteGeometry(
        positions=[[lon, lat] for lat, lon in coords],
        quality="approximate",
    )


class OSRMGeometryProvider:
    """Fetches road polylines from OSRM, falling back to straight lines."""

    name: str = "osrm_geometry"

    def __init__(
        self,
        host: str = "http://localhost:5000",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout

    def fetch(self, coords: list[tuple[float, float]]) -> RouteGeometry:
        """Road path through `coords` (lat, lon) in order.

        Never raises: every failure degrades to `straight_line`, because a
        missing polyline is a cosmetic loss while a raised exception here would
        discard a completed analysis.
        """
        if len(coords) < 2:
            return straight_line(coords)

        if len(coords) > MAX_WAYPOINTS:
            logger.info(
                "osrm_route_too_many_waypoints",
                n_waypoints=len(coords),
                max_waypoints=MAX_WAYPOINTS,
            )
            return straight_line(coords)

        coords_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
        url = (
            f"{self.host}/route/v1/driving/{coords_str}"
            f"?geometries=geojson&overview=full&steps=false"
        )

        try:
            response = httpx.get(url, timeout=self.timeout)
        except httpx.HTTPError as exc:
            logger.warning("osrm_route_unreachable", error=str(exc), n_waypoints=len(coords))
            return straight_line(coords)

        if response.status_code != 200:
            logger.warning(
                "osrm_route_http_error",
                status=response.status_code,
                n_waypoints=len(coords),
            )
            return straight_line(coords)

        try:
            data = response.json()
        except ValueError:
            logger.warning("osrm_route_bad_json")
            return straight_line(coords)

        if data.get("code") != "Ok":
            logger.warning("osrm_route_not_ok", osrm_code=data.get("code"))
            return straight_line(coords)

        routes = data.get("routes") or []
        if not routes:
            logger.warning("osrm_route_empty")
            return straight_line(coords)

        geometry = routes[0].get("geometry") or {}
        positions = geometry.get("coordinates") or []
        if not positions:
            logger.warning("osrm_route_no_coordinates")
            return straight_line(coords)

        # OSRM already returns [lon, lat] pairs, matching GeoJSON.
        return RouteGeometry(
            positions=[[float(lon), float(lat)] for lon, lat in positions],
            quality="exact",
        )


class StraightLineGeometryProvider:
    """Always straight segments. For tests and OSRM-free runs."""

    name: str = "straight_line"

    def fetch(self, coords: list[tuple[float, float]]) -> RouteGeometry:
        return straight_line(coords)
