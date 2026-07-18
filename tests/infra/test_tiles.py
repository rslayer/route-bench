"""Basemap tile sourcing.

The module exists so that where tiles come from is a decision made in one place
and consumed by whoever needs it, rather than a library default nobody chose.
These pin the parts that were previously wrong by omission.
"""

from __future__ import annotations

from routebench.analysis.visuals.maps import render_route_map
from routebench.core.config import Settings
from routebench.infra.tiles import (
    OSM_TILE_URL,
    custom_tiles,
    from_settings,
    no_tiles,
    osm_tiles,
)

_ROUTE = {
    "depot_lat": 32.7767,
    "depot_lon": -96.7970,
    "stops": [
        {"latitude": 32.78 + i * 0.01, "longitude": -96.80 - i * 0.01, "stop_sequence": i}
        for i in (1, 2, 3)
    ],
}


class TestTileSource:
    def test_osm_identifies_this_application(self) -> None:
        """staticmap's default User-Agent is the string "StaticMap". The OSM
        tile usage policy requires a UA identifying the application, and generic
        clients get blocked — so a public deployment must not send the default."""
        source = osm_tiles()
        assert source.user_agent.startswith("RouteBench/")
        assert "github.com/rslayer/route-bench" in source.user_agent
        assert source.headers()["User-Agent"] == source.user_agent

    def test_osm_has_a_finite_timeout(self) -> None:
        """staticmap defaults tile_request_timeout to None — no timeout at all.
        A slow tile host would hang report rendering indefinitely."""
        assert osm_tiles().timeout_seconds > 0
        assert custom_tiles("https://x/{z}/{x}/{y}.png").timeout_seconds > 0

    def test_no_tiles_is_a_supported_mode_not_an_error(self) -> None:
        source = no_tiles()
        assert source.enabled is False
        assert source.url_template is None

    def test_enabled_reflects_whether_a_url_is_set(self) -> None:
        assert osm_tiles().enabled is True
        assert custom_tiles("https://tiles.example/{z}/{x}/{y}.png").enabled is True


class TestFromSettings:
    def test_tiles_are_off_by_default(self) -> None:
        """A default that reaches out to a donated public service on every
        report is not something to opt a deployment into silently."""
        assert Settings().map_tiles_enabled is False
        assert from_settings(Settings()).enabled is False

    def test_enabling_uses_the_osm_defaults(self) -> None:
        source = from_settings(Settings(map_tiles_enabled=True))
        assert source.name == "osm"
        assert source.url_template == OSM_TILE_URL

    def test_a_custom_host_is_honoured(self) -> None:
        source = from_settings(
            Settings(
                map_tiles_enabled=True,
                map_tile_url="https://tiles.internal/{z}/{x}/{y}.png",
                map_tile_attribution="Internal",
            )
        )
        assert source.name == "custom"
        assert source.url_template == "https://tiles.internal/{z}/{x}/{y}.png"
        assert source.attribution == "Internal"

    def test_an_empty_url_disables_rather_than_producing_a_broken_source(self) -> None:
        source = from_settings(Settings(map_tiles_enabled=True, map_tile_url=""))
        assert source.enabled is False


class TestRenderingWithoutABasemap:
    def test_offline_render_still_produces_an_image(self) -> None:
        """The whole point: no network, but the reader still gets route geometry.
        Previously an unreachable tile server meant render_route_map returned ""
        and the figure vanished from the report entirely."""
        img = render_route_map(_ROUTE, tile_source=no_tiles())
        assert img.startswith("data:image/png;base64,")
        assert len(img) > 1000, "suspiciously small image — did the route draw?"

    def test_offline_render_makes_no_network_call(self, monkeypatch) -> None:
        """Guards the reason the test suite stopped hammering openstreetmap.org."""
        import staticmap

        def _explode(*args: object, **kwargs: object) -> None:
            msg = "no network call should happen with tiles disabled"
            raise AssertionError(msg)

        monkeypatch.setattr(staticmap.StaticMap, "get", _explode, raising=False)
        img = render_route_map(_ROUTE, tile_source=no_tiles())
        assert img.startswith("data:image/png;base64,")

    def test_unreachable_tile_server_degrades_instead_of_losing_the_map(self) -> None:
        """An unreachable basemap must not cost the reader the route. Same
        reasoning as the matrix layer's straight-line fallback."""
        dead = custom_tiles("http://127.0.0.1:59998/{z}/{x}/{y}.png", timeout_seconds=0.05)
        img = render_route_map(_ROUTE, tile_source=dead)
        assert img.startswith("data:image/png;base64,")

    def test_returns_empty_only_when_there_is_nothing_to_draw(self) -> None:
        assert render_route_map({"stops": []}, tile_source=no_tiles()) == ""
