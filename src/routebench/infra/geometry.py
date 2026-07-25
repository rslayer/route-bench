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


# Google Routes computeRoutes returns the driven path directly as a GeoJSON
# LineString when asked, so no encoded-polyline decoder is needed.
_GOOGLE_ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"

# computeRoutes accepts at most 25 intermediate waypoints per call. A route with
# more stops is fetched in consecutive chunks that share a boundary stop and are
# stitched back together, so long routes still draw a continuous road path.
_MAX_INTERMEDIATES = 25

# Only the geometry is requested. The field mask is required — computeRoutes
# refuses a request without one.
_GOOGLE_ROUTES_FIELD_MASK = "routes.polyline.geoJsonLinestring"


class GoogleGeometryProvider:
    """Road polylines from Google Routes computeRoutes, falling back to straight lines.

    The geometry counterpart to `GoogleMatrixProvider`: when the deployment runs
    on the Google engine there is no OSRM sidecar to draw road paths, so this
    fetches them from the same Routes API (and the same restricted key). It uses
    the plain DRIVE route — geometry does not need the traffic-aware tier — so it
    bills at the cheaper Compute Routes rate, roughly one call per route.

    Never raises, exactly like `OSRMGeometryProvider`: any failure degrades to
    straight segments, because a missing polyline is cosmetic while a raised
    exception here would discard a completed analysis.
    """

    name: str = "google_geometry"

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, coords: list[tuple[float, float]]) -> RouteGeometry:
        """Road path through `coords` (lat, lon) in order.

        Splits into <=25-intermediate chunks, fetches each, and stitches them.
        A chunk that fails degrades to straight segments for that span only; the
        whole line is tagged "approximate" if any chunk did so, "exact" if all
        succeeded.
        """
        if len(coords) < 2:
            return straight_line(coords)

        positions: list[Position] = []
        all_exact = True
        for chunk in _chunk_waypoints(coords, _MAX_INTERMEDIATES):
            segment = self._fetch_chunk(chunk)
            if segment is None:
                segment = [[lon, lat] for lat, lon in chunk]
                all_exact = False
            # Drop the first point of every chunk after the first: it is the
            # previous chunk's shared boundary stop, already in `positions`.
            positions.extend(segment if not positions else segment[1:])

        if not positions:
            return straight_line(coords)
        return RouteGeometry(positions=positions, quality="exact" if all_exact else "approximate")

    def _fetch_chunk(self, chunk: list[tuple[float, float]]) -> list[Position] | None:
        """One computeRoutes call for one chunk. None on any failure."""
        body = {
            "origin": _waypoint(chunk[0]),
            "destination": _waypoint(chunk[-1]),
            "intermediates": [_waypoint(c) for c in chunk[1:-1]],
            "travelMode": "DRIVE",
            "polylineEncoding": "GEO_JSON_LINESTRING",
            "polylineQuality": "HIGH_QUALITY",
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": _GOOGLE_ROUTES_FIELD_MASK,
        }
        try:
            response = httpx.post(
                _GOOGLE_ROUTES_ENDPOINT, json=body, headers=headers, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("google_geometry_unreachable", error=str(exc), n_waypoints=len(chunk))
            return None

        if response.status_code != 200:
            logger.warning(
                "google_geometry_http_error", status=response.status_code, n_waypoints=len(chunk)
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning("google_geometry_bad_json")
            return None

        routes = data.get("routes") or []
        if not routes:
            # A legitimately unroutable chunk returns 200 with no routes.
            logger.warning("google_geometry_empty", n_waypoints=len(chunk))
            return None

        line = (routes[0].get("polyline") or {}).get("geoJsonLinestring") or {}
        raw = line.get("coordinates") or []
        if len(raw) < 2:
            logger.warning("google_geometry_no_coordinates")
            return None

        # Google returns [lon, lat] pairs, matching GeoJSON.
        return [[float(lon), float(lat)] for lon, lat in raw]


def build_geometry_provider(
    matrix_engine: str, google_maps_api_key: str, osrm_host: str
) -> OSRMGeometryProvider | GoogleGeometryProvider | StraightLineGeometryProvider:
    """Pick the geometry provider that matches the deployment's routing engine.

    On the Google engine there is no OSRM sidecar, so geometry comes from the
    Routes API using the same key. If the engine is Google but no key is set —
    a misconfiguration the matrix layer would already have rejected at startup —
    fall back to straight lines rather than pointing at an OSRM host that is not
    there, so the map still draws (as a sketch) instead of logging a failure per
    route.
    """
    if matrix_engine == "google":
        if google_maps_api_key:
            return GoogleGeometryProvider(api_key=google_maps_api_key)
        return StraightLineGeometryProvider()
    return OSRMGeometryProvider(host=osrm_host)


def _waypoint(coord: tuple[float, float]) -> dict[str, object]:
    """A (lat, lon) tuple as a Routes API waypoint."""
    lat, lon = coord
    return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}


def _chunk_waypoints(
    coords: list[tuple[float, float]], max_intermediates: int
) -> list[list[tuple[float, float]]]:
    """Split ordered coords into chunks of at most `max_intermediates` intermediates.

    Consecutive chunks overlap by one stop — the boundary stop is the previous
    chunk's destination and the next chunk's origin — so stitching their
    polylines yields one continuous path with no gap at the seam.
    """
    step = max_intermediates + 1  # points consumed per chunk before the overlap
    if len(coords) <= step + 1:
        return [coords]
    chunks: list[list[tuple[float, float]]] = []
    start = 0
    while start < len(coords) - 1:
        chunks.append(coords[start : start + step + 1])
        start += step
    return chunks
