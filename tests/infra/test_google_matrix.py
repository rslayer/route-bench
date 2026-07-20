"""Tests for GoogleMatrixProvider.

Can't hit the real API in tests (it costs money and needs a key), so httpx.post
is stubbed and the provider is exercised against synthetic Google responses. The
point is to pin the two things that break silently: request construction and the
placement of out-of-order / missing elements into the matrix.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from routebench.core.exceptions import MatrixUnavailableError
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.google import GoogleMatrixProvider, estimate_run_cost_usd


class _FakeResponse:
    def __init__(self, status_code: int, body: Any, *, text: str | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _element(oi: int, di: int, seconds: float, meters: float) -> dict[str, Any]:
    return {
        "originIndex": oi,
        "destinationIndex": di,
        "duration": f"{seconds:g}s",
        "distanceMeters": meters,
        "condition": "ROUTE_EXISTS",
        "status": {},
    }


def _install_post(monkeypatch: pytest.MonkeyPatch, responder: Any) -> list[dict[str, Any]]:
    """Patch httpx.post. `responder` is called with the request body dict and
    returns a _FakeResponse. Records every request body for assertions."""
    calls: list[dict[str, Any]] = []

    def _post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        calls.append({"url": url, "json": json, "headers": headers})
        return responder(json)

    monkeypatch.setattr(httpx, "post", _post)
    return calls


class TestHappyPath:
    def test_places_elements_by_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A 2x2 matrix, elements returned OUT OF ORDER on purpose.
        body = [
            _element(1, 0, 300, 5000),
            _element(0, 0, 0, 0),
            _element(1, 1, 0, 0),
            _element(0, 1, 600, 9000),
        ]
        _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        provider = GoogleMatrixProvider(api_key="k")

        result = provider.get_matrix([(32.8, -96.8), (32.9, -96.7)], [(32.8, -96.8), (32.9, -96.7)])

        assert result.durations_seconds == [[0.0, 600.0], [300.0, 0.0]]
        assert result.distances_meters == [[0.0, 9000.0], [5000.0, 0.0]]
        assert result.provider == "google"
        assert result.approximate is False

    def test_cost_estimate_scales_with_elements(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = [_element(i, j, 10, 100) for i in range(2) for j in range(3)]
        _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        provider = GoogleMatrixProvider(api_key="k")

        result = provider.get_matrix([(0.0, 0.0)] * 2, [(0.0, 0.0)] * 3)
        # 6 elements x $0.01
        assert result.cost_estimate == pytest.approx(0.06)

    def test_request_is_traffic_aware_with_key_and_field_mask(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = [_element(0, 0, 0, 0)]
        calls = _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        GoogleMatrixProvider(api_key="secret-key").get_matrix([(1.0, 2.0)], [(1.0, 2.0)])

        req = calls[0]
        assert req["json"]["routingPreference"] == "TRAFFIC_AWARE"
        assert req["json"]["travelMode"] == "DRIVE"
        assert req["headers"]["X-Goog-Api-Key"] == "secret-key"
        assert "duration" in req["headers"]["X-Goog-FieldMask"]
        # latLng shape, not lon,lat strings
        assert req["json"]["origins"][0]["waypoint"]["location"]["latLng"] == {
            "latitude": 1.0,
            "longitude": 2.0,
        }


class TestUnreachablePairs:
    def test_route_not_found_is_infinity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = [
            _element(0, 0, 0, 0),
            {"originIndex": 0, "destinationIndex": 1, "condition": "ROUTE_NOT_FOUND"},
        ]
        _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        result = GoogleMatrixProvider(api_key="k").get_matrix(
            [(0.0, 0.0)], [(0.0, 0.0), (9.0, 9.0)]
        )
        assert result.durations_seconds[0][1] == float("inf")

    def test_missing_element_stays_infinity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Google omits the (0,1) element entirely; it must not read as 0.
        body = [_element(0, 0, 100, 1000)]
        _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        result = GoogleMatrixProvider(api_key="k").get_matrix(
            [(0.0, 0.0)], [(0.0, 0.0), (9.0, 9.0)]
        )
        assert result.durations_seconds[0][0] == 100.0
        assert result.durations_seconds[0][1] == float("inf")

    def test_per_element_error_status_is_infinity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = [
            {
                "originIndex": 0,
                "destinationIndex": 0,
                "status": {"code": 3, "message": "bad"},
            }
        ]
        _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        result = GoogleMatrixProvider(api_key="k").get_matrix([(0.0, 0.0)], [(0.0, 0.0)])
        assert result.durations_seconds[0][0] == float("inf")


class TestChunking:
    def test_large_matrix_splits_and_stitches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 30 origins x 30 destinations = 900 elements > the 625 cap, so it must
        # split into 2x2 = 4 chunks and reassemble into the full 30x30.
        def responder(req: dict[str, Any]) -> _FakeResponse:
            n_o = len(req["origins"])
            n_d = len(req["destinations"])
            # Encode a globally-unique value via the request's coordinates so we
            # can verify each cell landed in the right place after stitching.
            body = []
            for i in range(n_o):
                for j in range(n_d):
                    o_lat = req["origins"][i]["waypoint"]["location"]["latLng"]["latitude"]
                    d_lat = req["destinations"][j]["waypoint"]["location"]["latLng"]["latitude"]
                    body.append(_element(i, j, o_lat * 1000 + d_lat, 1.0))
            return _FakeResponse(200, body)

        calls = _install_post(monkeypatch, responder)
        origins = [(float(i), 0.0) for i in range(30)]
        destinations = [(float(j), 0.0) for j in range(30)]
        result = GoogleMatrixProvider(api_key="k").get_matrix(origins, destinations)

        assert len(calls) == 4  # 2 origin chunks x 2 dest chunks
        # Every cell (i,j) should equal i*1000 + j — proves correct placement.
        for i in range(30):
            for j in range(30):
                assert result.durations_seconds[i][j] == pytest.approx(i * 1000 + j)


class TestFailureModes:
    def test_non_200_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_post(
            monkeypatch, lambda _req: _FakeResponse(403, {"error": "denied"}, text="forbidden")
        )
        with pytest.raises(MatrixUnavailableError):
            GoogleMatrixProvider(api_key="k").get_matrix([(0.0, 0.0)], [(0.0, 0.0)])

    def test_timeout_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*a: Any, **k: Any) -> Any:
            raise httpx.TimeoutException("slow")

        monkeypatch.setattr(httpx, "post", _boom)
        with pytest.raises(MatrixUnavailableError):
            GoogleMatrixProvider(api_key="k").get_matrix([(0.0, 0.0)], [(0.0, 0.0)])

    def test_non_json_body_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_post(
            monkeypatch,
            lambda _req: _FakeResponse(200, ValueError("not json"), text="<html>"),
        )
        with pytest.raises(MatrixUnavailableError):
            GoogleMatrixProvider(api_key="k").get_matrix([(0.0, 0.0)], [(0.0, 0.0)])

    def test_non_list_body_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_post(monkeypatch, lambda _req: _FakeResponse(200, {"unexpected": "object"}))
        with pytest.raises(MatrixUnavailableError):
            GoogleMatrixProvider(api_key="k").get_matrix([(0.0, 0.0)], [(0.0, 0.0)])

    def test_empty_key_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            GoogleMatrixProvider(api_key="")


class TestDepartureTime:
    def test_past_time_rolled_forward_preserving_weekday_and_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = [_element(0, 0, 0, 0)]
        calls = _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        # 2020-01-06 was a Monday, 08:00 UTC — firmly in the past.
        past_monday_8am = datetime(2020, 1, 6, 8, 0, tzinfo=UTC)
        GoogleMatrixProvider(api_key="k").get_matrix(
            [(0.0, 0.0)], [(0.0, 0.0)], departure_time=past_monday_8am
        )

        sent = datetime.strptime(calls[0]["json"]["departureTime"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        assert sent > datetime.now(UTC)
        assert sent.weekday() == 0  # still a Monday
        assert sent.hour == 8  # still 08:00

    def test_future_time_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = [_element(0, 0, 0, 0)]
        calls = _install_post(monkeypatch, lambda _req: _FakeResponse(200, body))
        future = datetime.now(UTC) + timedelta(days=3)
        GoogleMatrixProvider(api_key="k").get_matrix(
            [(0.0, 0.0)], [(0.0, 0.0)], departure_time=future
        )
        sent = datetime.strptime(calls[0]["json"]["departureTime"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        # Same day, within the week — not rolled.
        assert abs((sent - future).total_seconds()) < 60


def _route(route_id: str, n_stops: int) -> Route:
    from datetime import UTC, datetime

    return Route(
        route_id=route_id,
        stops=[
            Stop(route_id=route_id, stop_sequence=i, latitude=32.8, longitude=-96.8)
            for i in range(1, n_stops + 1)
        ],
        depot_lat=32.79,
        depot_lon=-96.80,
        planned_start_time=datetime(2025, 1, 1, 8, 0, tzinfo=UTC),
    )


def _fleet(*sizes: int) -> Fleet:
    from datetime import UTC, datetime

    return Fleet(
        routes=[_route(f"R{i}", n) for i, n in enumerate(sizes)],
        upload_id="t",
        uploaded_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


class TestCostEstimate:
    def test_single_route_with_benchmark(self) -> None:
        # 1 route, 3 stops: per-route (3+1)^2 = 16; fleet (3+1)^2 = 16; total 32.
        assert estimate_run_cost_usd(_fleet(3), include_benchmark=True) == pytest.approx(0.32)

    def test_single_route_without_benchmark(self) -> None:
        # No fleet matrix: just the per-route 16 elements.
        assert estimate_run_cost_usd(_fleet(3), include_benchmark=False) == pytest.approx(0.16)

    def test_multi_route(self) -> None:
        # routes 3 and 4 stops: (4)^2 + (5)^2 = 41; fleet total_stops 7 -> (8)^2 = 64; 105.
        assert estimate_run_cost_usd(_fleet(3, 4), include_benchmark=True) == pytest.approx(1.05)

    def test_grows_with_the_square_of_fleet_size(self) -> None:
        small = estimate_run_cost_usd(_fleet(10), include_benchmark=True)
        big = estimate_run_cost_usd(_fleet(20), include_benchmark=True)
        assert big > 3 * small  # quadratic, not linear
