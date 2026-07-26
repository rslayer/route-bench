"""Phase 11 Task 0: OSRM route geometry, and its graceful degradation.

The contract that matters: this never raises. A geometry failure is a cosmetic
loss — the map draws a sketch instead of a road path — while an exception here
would discard an analysis that already produced real findings.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from routebench.infra.geometry import (
    MAX_WAYPOINTS,
    GoogleGeometryProvider,
    OSRMGeometryProvider,
    StraightLineGeometryProvider,
    build_geometry_provider,
    straight_line,
)

DALLAS = [(32.79, -96.80), (32.81, -96.78), (32.83, -96.76)]


def _osrm_ok(coords: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": "Ok",
            "routes": [{"geometry": {"type": "LineString", "coordinates": coords}}],
        },
    )


class TestStraightLine:
    """The always-available fallback."""

    def test_converts_to_lon_lat(self) -> None:
        """GeoJSON is [lon, lat]; the codebase passes (lat, lon)."""
        geom = straight_line([(32.79, -96.80)])
        assert geom.positions == [[-96.80, 32.79]]

    def test_is_always_approximate(self) -> None:
        assert straight_line(DALLAS).quality == "approximate"

    def test_preserves_order(self) -> None:
        geom = straight_line(DALLAS)
        assert [p[1] for p in geom.positions] == [32.79, 32.81, 32.83]

    def test_provider_wraps_it(self) -> None:
        geom = StraightLineGeometryProvider().fetch(DALLAS)
        assert geom.quality == "approximate"
        assert len(geom.positions) == 3


class TestOSRMSuccess:
    def test_returns_exact_road_geometry(self) -> None:
        road = [[-96.80, 32.79], [-96.795, 32.80], [-96.78, 32.81]]
        with patch("httpx.get", return_value=_osrm_ok(road)):
            geom = OSRMGeometryProvider().fetch(DALLAS[:2])
        assert geom.quality == "exact"
        assert geom.positions == road

    def test_requests_geojson_geometry(self) -> None:
        """Without geometries=geojson OSRM returns an encoded polyline."""
        with patch("httpx.get", return_value=_osrm_ok([[0.0, 0.0], [1.0, 1.0]])) as mock_get:
            OSRMGeometryProvider(host="http://osrm:5000").fetch(DALLAS[:2])
        url = mock_get.call_args[0][0]
        assert "geometries=geojson" in url
        assert "/route/v1/driving/" in url

    def test_sends_lon_lat_in_the_url(self) -> None:
        with patch("httpx.get", return_value=_osrm_ok([[0.0, 0.0], [1.0, 1.0]])) as mock_get:
            OSRMGeometryProvider().fetch([(32.79, -96.80), (32.81, -96.78)])
        url = mock_get.call_args[0][0]
        assert "-96.8,32.79" in url


class TestOSRMDegradation:
    """Every failure mode must yield a drawable line, never an exception."""

    def _assert_fell_back(self, response_or_exc: Any) -> None:
        kwargs = (
            {"side_effect": response_or_exc}
            if isinstance(response_or_exc, Exception)
            else {"return_value": response_or_exc}
        )
        with patch("httpx.get", **kwargs):
            geom = OSRMGeometryProvider().fetch(DALLAS)
        assert geom.quality == "approximate"
        assert len(geom.positions) == len(DALLAS), "fallback must still be drawable"

    def test_connection_error(self) -> None:
        self._assert_fell_back(httpx.ConnectError("refused"))

    def test_timeout(self) -> None:
        self._assert_fell_back(httpx.TimeoutException("slow"))

    def test_http_500(self) -> None:
        self._assert_fell_back(httpx.Response(500, text="boom"))

    def test_http_400_too_many_coordinates(self) -> None:
        self._assert_fell_back(httpx.Response(400, json={"code": "TooBig"}))

    def test_non_json_body(self) -> None:
        self._assert_fell_back(httpx.Response(200, text="<html>nope</html>"))

    def test_osrm_no_route_code(self) -> None:
        self._assert_fell_back(httpx.Response(200, json={"code": "NoRoute"}))

    def test_empty_routes_list(self) -> None:
        self._assert_fell_back(httpx.Response(200, json={"code": "Ok", "routes": []}))

    def test_route_without_geometry(self) -> None:
        self._assert_fell_back(httpx.Response(200, json={"code": "Ok", "routes": [{}]}))

    def test_geometry_without_coordinates(self) -> None:
        self._assert_fell_back(
            httpx.Response(200, json={"code": "Ok", "routes": [{"geometry": {}}]})
        )


class TestWaypointGuards:
    def test_single_point_skips_osrm(self) -> None:
        """Two waypoints minimum; asking OSRM for a route through one is nonsense."""
        with patch("httpx.get") as mock_get:
            geom = OSRMGeometryProvider().fetch([(32.79, -96.80)])
        mock_get.assert_not_called()
        assert geom.quality == "approximate"

    def test_empty_skips_osrm(self) -> None:
        with patch("httpx.get") as mock_get:
            geom = OSRMGeometryProvider().fetch([])
        mock_get.assert_not_called()
        assert geom.positions == []

    def test_over_max_waypoints_skips_the_doomed_call(self) -> None:
        """OSRM's default cap is 500; asking beyond it just earns a 400."""
        coords = [(32.0 + i * 0.001, -96.0) for i in range(MAX_WAYPOINTS + 1)]
        with patch("httpx.get") as mock_get:
            geom = OSRMGeometryProvider().fetch(coords)
        mock_get.assert_not_called()
        assert geom.quality == "approximate"
        assert len(geom.positions) == len(coords)


class TestGeoJSONIntegration:
    """The emitter must honour a provider's quality verdict."""

    def test_exact_geometry_reaches_the_collection(self) -> None:
        from tests.report.test_geojson import _report

        road = [[-96.80, 32.79], [-96.79, 32.80], [-96.80, 32.79]]
        with patch("httpx.get", return_value=_osrm_ok(road)):
            from routebench.report.geojson import build_routes_geojson

            gj = build_routes_geojson(_report(), OSRMGeometryProvider())

        assert gj["properties"]["geometry_quality"] == "exact"
        actual = next(f for f in gj["features"] if f["properties"]["kind"] == "actual")
        assert actual["properties"]["geometry_quality"] == "exact"
        assert actual["geometry"]["coordinates"] == road

    def test_osrm_down_still_produces_a_map(self) -> None:
        """The whole point of the fallback: a usable artifact with no OSRM."""
        from tests.report.test_geojson import _report

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            from routebench.report.geojson import build_routes_geojson

            gj = build_routes_geojson(_report(), OSRMGeometryProvider())

        assert gj["properties"]["geometry_quality"] == "approximate"
        assert gj["features"], "a map with no features is not a fallback, it is a failure"
        assert "straight segments" in gj["properties"]["geometry_note"]

    def test_mixed_quality_reports_approximate(self) -> None:
        """One unroutable route must not let the fleet claim exact geometry."""
        from tests.report.test_geojson import _report, _route

        responses = [
            _osrm_ok([[-96.80, 32.79], [-96.79, 32.80]]),
            httpx.Response(200, json={"code": "NoRoute"}),
        ]
        with patch("httpx.get", side_effect=responses):
            from routebench.report.geojson import build_routes_geojson

            gj = build_routes_geojson(
                _report(routes=[_route("R1"), _route("R2")]), OSRMGeometryProvider()
            )

        assert gj["properties"]["geometry_quality"] == "approximate"
        qualities = {
            f["properties"]["route_id"]: f["properties"]["geometry_quality"]
            for f in gj["features"]
            if f["properties"]["kind"] == "actual"
        }
        assert qualities == {"R1": "exact", "R2": "approximate"}


@pytest.mark.parametrize("n_stops", [2, 10])
def test_fallback_line_length_matches_waypoints(n_stops: int) -> None:
    coords = [(32.0 + i * 0.01, -96.0) for i in range(n_stops)]
    assert len(straight_line(coords).positions) == n_stops


def _google_ok(coords: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"routes": [{"polyline": {"geoJsonLinestring": {"coordinates": coords}}}]},
    )


class TestGoogleGeometrySuccess:
    def test_returns_exact_road_geometry(self) -> None:
        road = [[-96.80, 32.79], [-96.79, 32.80], [-96.78, 32.81]]
        with patch("httpx.post", return_value=_google_ok(road)):
            result = GoogleGeometryProvider(api_key="k").fetch(DALLAS)
        assert result.quality == "exact"
        assert result.positions == road

    def test_request_shape_is_traffic_free_geojson_with_key(self) -> None:
        with patch("httpx.post", return_value=_google_ok([[0.0, 0.0], [1.0, 1.0]])) as mock_post:
            GoogleGeometryProvider(api_key="secret").fetch([(0.0, 0.0), (1.0, 1.0)])
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["polylineEncoding"] == "GEO_JSON_LINESTRING"
        assert kwargs["json"]["travelMode"] == "DRIVE"
        # Geometry does not need the traffic-aware tier — no routingPreference.
        assert "routingPreference" not in kwargs["json"]
        assert kwargs["headers"]["X-Goog-Api-Key"] == "secret"
        assert "geoJsonLinestring" in kwargs["headers"]["X-Goog-FieldMask"]
        # (lat, lon) tuple becomes a latLng waypoint, not a lon,lat string.
        assert kwargs["json"]["origin"]["location"]["latLng"] == {
            "latitude": 0.0,
            "longitude": 0.0,
        }


class TestGoogleGeometryDegradation:
    """Never raises: every failure degrades to straight (approximate) segments."""

    def _assert_fell_back(self, **kwargs: Any) -> None:
        with patch("httpx.post", **kwargs):
            result = GoogleGeometryProvider(api_key="k").fetch(DALLAS)
        assert result.quality == "approximate"
        assert len(result.positions) == len(DALLAS)

    def test_connection_error(self) -> None:
        self._assert_fell_back(side_effect=httpx.ConnectError("refused"))

    def test_timeout(self) -> None:
        self._assert_fell_back(side_effect=httpx.TimeoutException("slow"))

    def test_http_403(self) -> None:
        self._assert_fell_back(return_value=httpx.Response(403, json={"error": "denied"}))

    def test_non_json_body(self) -> None:
        self._assert_fell_back(return_value=httpx.Response(200, text="<html>nope</html>"))

    def test_empty_routes(self) -> None:
        self._assert_fell_back(return_value=httpx.Response(200, json={"routes": []}))

    def test_missing_coordinates(self) -> None:
        self._assert_fell_back(
            return_value=httpx.Response(
                200, json={"routes": [{"polyline": {"geoJsonLinestring": {}}}]}
            )
        )

    def test_single_point_skips_google(self) -> None:
        with patch("httpx.post") as mock_post:
            result = GoogleGeometryProvider(api_key="k").fetch([(1.0, 2.0)])
        mock_post.assert_not_called()
        assert result.quality == "approximate"


class TestGoogleGeometryChunking:
    def test_long_route_splits_and_stitches_without_a_seam_duplicate(self) -> None:
        # 30 stops > the 25-intermediate cap, so it must split into 2 calls and
        # stitch. Each fake chunk returns its own waypoints as the "road" path;
        # the stitched result must be continuous with no repeated boundary point.
        coords = [(32.0 + i * 0.01, -96.0) for i in range(30)]

        def responder(url: str, *, json: dict[str, Any], **_: Any) -> httpx.Response:
            pts = [json["origin"], *json["intermediates"], json["destination"]]
            line = [
                [p["location"]["latLng"]["longitude"], p["location"]["latLng"]["latitude"]]
                for p in pts
            ]
            return _google_ok(line)

        with patch("httpx.post", side_effect=responder) as mock_post:
            result = GoogleGeometryProvider(api_key="k").fetch(coords)

        assert mock_post.call_count == 2  # split into two chunks
        assert result.quality == "exact"
        # One point per stop, in order, no duplicated seam.
        assert len(result.positions) == len(coords)
        assert result.positions[0] == [-96.0, 32.0]
        assert result.positions[-1] == [-96.0, 32.0 + 29 * 0.01]

    def test_one_failed_chunk_taints_whole_line_as_approximate(self) -> None:
        coords = [(32.0 + i * 0.01, -96.0) for i in range(30)]
        calls = {"n": 0}

        def responder(url: str, *, json: dict[str, Any], **_: Any) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                # First chunk succeeds: echo its waypoints as the road path.
                pts = [json["origin"], *json["intermediates"], json["destination"]]
                line = [
                    [p["location"]["latLng"]["longitude"], p["location"]["latLng"]["latitude"]]
                    for p in pts
                ]
                return _google_ok(line)
            return httpx.Response(500, text="boom")

        with patch("httpx.post", side_effect=responder):
            result = GoogleGeometryProvider(api_key="k").fetch(coords)
        # Second chunk fell back to straight segments -> whole line approximate,
        # but still continuous and covering every stop.
        assert result.quality == "approximate"
        assert len(result.positions) == len(coords)


class TestGeometryFactory:
    def test_google_engine_with_key_uses_google(self) -> None:
        provider = build_geometry_provider("google", "AIza-key", "http://osrm:5000")
        assert isinstance(provider, GoogleGeometryProvider)

    def test_google_engine_without_key_falls_back_to_straight_line(self) -> None:
        provider = build_geometry_provider("google", "", "http://osrm:5000")
        assert isinstance(provider, StraightLineGeometryProvider)

    def test_osrm_engine_uses_osrm(self) -> None:
        provider = build_geometry_provider("osrm", "", "http://osrm:5000")
        assert isinstance(provider, OSRMGeometryProvider)
        assert provider.host == "http://osrm:5000"
