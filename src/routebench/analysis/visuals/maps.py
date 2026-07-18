"""Map visualizations for route findings.

Draws the route itself; where the basemap underneath comes from is decided by
`routebench.infra.tiles`, which this asks rather than hard-coding.
"""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING, Any

import structlog

from routebench.infra.tiles import TileSource, no_tiles

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import Image

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# The module-level default. Overridden per call, and swapped wholesale by the
# test suite so the suite never reaches for the network.
_DEFAULT_TILE_SOURCE: TileSource = no_tiles()


def set_default_tile_source(source: TileSource) -> None:
    """Set the basemap used when a caller does not name one."""
    global _DEFAULT_TILE_SOURCE
    _DEFAULT_TILE_SOURCE = source


def default_tile_source() -> TileSource:
    """The basemap used when a caller does not name one."""
    return _DEFAULT_TILE_SOURCE


def _build_static_map(width: int, height: int, source: TileSource) -> Any:
    """A StaticMap wired to `source`, including the no-basemap case.

    staticmap always fetches tiles in `_draw_base_layer` and raises if it cannot
    get them, so "draw without a basemap" is expressed by overriding that one
    method to leave the background as-is. The rest of render() — lines, markers,
    bounds — is untouched.
    """
    from staticmap import StaticMap

    if not source.enabled:
        # staticmap ships no type information, so StaticMap is Any here and
        # mypy refuses the subclass on principle. The override itself is the
        # supported way to render without a basemap.
        class _BlankBase(StaticMap):  # type: ignore[misc]
            def _draw_base_layer(self, image: Image.Image) -> None:
                return None

        return _BlankBase(width, height, background_color="#f5f5f5")

    return StaticMap(
        width,
        height,
        url_template=source.url_template,
        tile_size=source.tile_size,
        tile_request_timeout=source.timeout_seconds,
        headers=source.headers(),
    )


def _draw_route(
    m: Any,
    route_data: dict[str, Any],
    flagged_stops: list[dict[str, Any]] | None,
) -> bool:
    """Add the depot, stops and route line. False if there is nothing to draw."""
    from staticmap import CircleMarker, Line

    depot_lat = route_data.get("depot_lat")
    depot_lon = route_data.get("depot_lon")
    if depot_lat is None or depot_lon is None:
        return False

    m.add_marker(CircleMarker((depot_lon, depot_lat), "red", 8))

    coords: list[tuple[float, float]] = [(depot_lon, depot_lat)]

    flagged_seqs: set[int] = set()
    for fs in flagged_stops or []:
        seq = fs.get("stop_sequence")
        if seq is not None:
            flagged_seqs.add(seq)

    for stop in route_data.get("stops", []):
        lat = stop.get("latitude")
        lon = stop.get("longitude")
        if lat is None or lon is None:
            continue
        coords.append((lon, lat))
        if stop.get("stop_sequence", 0) in flagged_seqs:
            m.add_marker(CircleMarker((lon, lat), "orange", 6))
        else:
            m.add_marker(CircleMarker((lon, lat), "#2563eb", 4))

    coords.append((depot_lon, depot_lat))
    if len(coords) > 1:
        m.add_line(Line(coords, "#2563eb", 2))
    return True


def _encode(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def render_route_map(
    route_data: dict[str, Any],
    flagged_stops: list[dict[str, Any]] | None = None,
    width: int = 400,
    height: int = 300,
    tile_source: TileSource | None = None,
) -> str:
    """Render a route map as a base64-encoded PNG data URI.

    Returns an empty string only when there is genuinely nothing to draw. An
    unreachable tile server is NOT that case: it falls back to drawing the route
    on a plain background, because the geometry is the part the reader needs and
    losing the whole figure over a missing basemap is a bad trade.

    Args:
        route_data: Dict with depot_lat, depot_lon, and a stops list.
        flagged_stops: Optional stops to highlight.
        width: Image width in pixels.
        height: Image height in pixels.
        tile_source: Basemap to draw under the route. Defaults to the
            process-wide source, which is "no basemap" unless configured.
    """
    source = tile_source or default_tile_source()

    try:
        m = _build_static_map(width, height, source)
        if not _draw_route(m, route_data, flagged_stops):
            return ""
        return _encode(m.render())
    except Exception:
        if not source.enabled:
            # Nothing to retry: the failure was local, not the network.
            logger.exception("map_render_error", tile_source=source.name)
            return ""

        # staticmap raises after exhausting its tile retries. Redraw with no
        # basemap rather than dropping the figure — same reasoning as the
        # matrix layer's straight-line fallback: a labelled degradation beats
        # an absence the reader cannot interpret.
        logger.warning(
            "basemap_unavailable_drawing_without_tiles",
            tile_source=source.name,
            url_template=source.url_template,
        )
        try:
            blank = _build_static_map(width, height, no_tiles())
            if not _draw_route(blank, route_data, flagged_stops):
                return ""
            return _encode(blank.render())
        except Exception:
            logger.exception("map_render_error", tile_source=source.name)
            return ""
