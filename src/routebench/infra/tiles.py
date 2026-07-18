"""Basemap tile sourcing — one place that decides where map tiles come from.

Separated from the renderer on purpose. A `TileSource` is a *description* of a
basemap (where the tiles live, how to identify ourselves, how long to wait), not
a renderer, so any component can consume it: the report's static PNGs today, and
anything that later needs to name, attribute, or configure the basemap without
taking a dependency on the drawing library.

Three things this centralises that were previously left to library defaults:

- **Identification.** staticmap sends `User-Agent: StaticMap`. The OSM tile
  usage policy requires a User-Agent that identifies the application, and
  generic clients get blocked. A public deployment hitting the shared tile
  servers anonymously is asking to be cut off.
- **A timeout.** staticmap defaults to `tile_request_timeout=None`, meaning no
  timeout at all. A slow tile host would hang report rendering indefinitely.
- **An off switch.** Tests, air-gapped installs, and anyone who would rather not
  depend on a third-party service need to render routes with no basemap. That is
  a supported mode here, not a failure.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from routebench.core.version import package_version

# Sent on every tile request. The OSM tile usage policy asks for a User-Agent
# that identifies the application and offers a way to make contact; the repo URL
# is that contact point.
_CONTACT_URL = "https://github.com/rslayer/route-bench"

OSM_TILE_URL = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def _user_agent() -> str:
    return f"RouteBench/{package_version()} (+{_CONTACT_URL})"


class TileSource(BaseModel):
    """Where basemap tiles come from, and the terms of asking for them.

    `url_template` of None means "no basemap": routes are drawn on a plain
    background and no network request is made. That is a first-class mode, so
    callers should branch on `enabled` rather than treating None as an error.
    """

    name: str
    url_template: str | None = None
    tile_size: int = 256
    attribution: str = ""
    timeout_seconds: float = 5.0
    user_agent: str = Field(default_factory=_user_agent)

    @property
    def enabled(self) -> bool:
        """True when this source will actually fetch tiles."""
        return bool(self.url_template)

    def headers(self) -> dict[str, str]:
        """Request headers identifying this application to the tile server."""
        return {"User-Agent": self.user_agent}


def osm_tiles(timeout_seconds: float = 5.0) -> TileSource:
    """The public OpenStreetMap tile servers.

    Free and rate-limited. Fine for low volume; a deployment doing real traffic
    should point `custom_tiles` at its own tile host rather than leaning on a
    donated service.
    """
    return TileSource(
        name="osm",
        url_template=OSM_TILE_URL,
        attribution=OSM_ATTRIBUTION,
        timeout_seconds=timeout_seconds,
    )


def no_tiles() -> TileSource:
    """No basemap: routes on a plain background, no network request.

    Not a degraded mode to apologise for — the route geometry, the stop markers
    and their relative positions all still read without a basemap underneath.
    """
    return TileSource(name="none", url_template=None, attribution="")


def custom_tiles(
    url_template: str,
    attribution: str = "",
    timeout_seconds: float = 5.0,
) -> TileSource:
    """A self-hosted or commercial tile server, given a `{z}/{x}/{y}` template."""
    return TileSource(
        name="custom",
        url_template=url_template,
        attribution=attribution,
        timeout_seconds=timeout_seconds,
    )


def from_settings(settings: object) -> TileSource:
    """Build the configured tile source from Settings.

    Takes `object` rather than importing Settings, so this module stays a leaf
    that anything can import without dragging in application configuration.
    """
    enabled = bool(getattr(settings, "map_tiles_enabled", True))
    if not enabled:
        return no_tiles()

    url = str(getattr(settings, "map_tile_url", OSM_TILE_URL) or "")
    if not url:
        return no_tiles()

    timeout = float(getattr(settings, "map_tile_timeout_seconds", 5.0))
    attribution = str(getattr(settings, "map_tile_attribution", OSM_ATTRIBUTION))
    if url == OSM_TILE_URL:
        return osm_tiles(timeout_seconds=timeout)
    return custom_tiles(url, attribution=attribution, timeout_seconds=timeout)
