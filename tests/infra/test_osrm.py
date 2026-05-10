"""Tests for infra/matrix/osrm.py — OSRM client with recorded HTTP fixtures.

Uses httpx mock transport to simulate OSRM responses without a real server.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from routebench.core.exceptions import MatrixUnavailableError
from routebench.infra.matrix.osrm import OSRMMatrixProvider, MAX_CELLS_PER_REQUEST


def _make_osrm_response(
    n_origins: int, n_destinations: int, base_duration: float = 100.0, base_distance: float = 5000.0
) -> dict[str, Any]:
    """Create a mock OSRM table response."""
    return {
        "code": "Ok",
        "durations": [
            [base_duration * (i + j + 1) for j in range(n_destinations)]
            for i in range(n_origins)
        ],
        "distances": [
            [base_distance * (i + j + 1) for j in range(n_destinations)]
            for i in range(n_origins)
        ],
    }


class MockTransport(httpx.BaseTransport):
    """Mock transport that returns pre-configured OSRM responses."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses = responses or []
        self._call_count = 0
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return httpx.Response(200, json=resp)
        return httpx.Response(200, json=_make_osrm_response(2, 2))


class TestOSRMSingleRequest:
    """Tests for single (non-chunked) OSRM requests."""

    def test_basic_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Basic 2x2 matrix request returns correct structure."""
        mock_response = _make_osrm_response(2, 3)
        transport = MockTransport([mock_response])

        # Patch httpx.get to use our mock transport
        client = httpx.Client(transport=transport)
        monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url))

        provider = OSRMMatrixProvider(host="http://localhost:5000")
        origins = [(32.82, -96.77), (32.83, -96.76)]
        destinations = [(32.84, -96.75), (32.85, -96.74), (32.86, -96.73)]

        result = provider.get_matrix(origins, destinations)

        assert result.provider == "osrm"
        assert result.cached is False
        assert len(result.durations_seconds) == 2
        assert len(result.durations_seconds[0]) == 3
        assert len(result.distances_meters) == 2
        assert len(result.distances_meters[0]) == 3

    def test_numpy_arrays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """durations_array() and distances_array() return proper numpy arrays."""
        mock_response = _make_osrm_response(2, 2)
        transport = MockTransport([mock_response])
        client = httpx.Client(transport=transport)
        monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url))

        provider = OSRMMatrixProvider(host="http://localhost:5000")
        result = provider.get_matrix(
            [(32.82, -96.77), (32.83, -96.76)],
            [(32.84, -96.75), (32.85, -96.74)],
        )

        dur_arr = result.durations_array()
        dist_arr = result.distances_array()
        assert dur_arr.shape == (2, 2)
        assert dist_arr.shape == (2, 2)

    def test_connection_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connection error raises MatrixUnavailableError."""

        def _raise_connect_error(*args: object, **kwargs: object) -> None:
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr(httpx, "get", _raise_connect_error)

        provider = OSRMMatrixProvider(host="http://localhost:5000")
        with pytest.raises(MatrixUnavailableError, match="Could not connect"):
            provider.get_matrix([(32.82, -96.77)], [(32.84, -96.75)])

    def test_osrm_error_code_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSRM error code raises MatrixUnavailableError."""
        error_response = {"code": "InvalidQuery", "message": "Bad coordinates"}
        transport = MockTransport([error_response])
        client = httpx.Client(transport=transport)
        monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url))

        provider = OSRMMatrixProvider(host="http://localhost:5000")
        with pytest.raises(MatrixUnavailableError, match="OSRM error"):
            provider.get_matrix([(32.82, -96.77)], [(32.84, -96.75)])


class TestOSRMChunking:
    """Tests for chunked matrix requests (>10K cells)."""

    def test_large_matrix_is_chunked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 200x200 matrix (40K cells) gets split into chunks."""
        # Track how many HTTP calls are made
        call_count = 0
        original_single = OSRMMatrixProvider._single_request

        def counting_single(
            self: OSRMMatrixProvider,
            origins: list[tuple[float, float]],
            destinations: list[tuple[float, float]],
        ) -> Any:
            nonlocal call_count
            call_count += 1
            n_o = len(origins)
            n_d = len(destinations)
            return _make_mock_result(n_o, n_d)

        monkeypatch.setattr(OSRMMatrixProvider, "_single_request", counting_single)

        provider = OSRMMatrixProvider(host="http://localhost:5000")
        origins = [(32.0 + i * 0.001, -96.0) for i in range(200)]
        destinations = [(33.0 + i * 0.001, -95.0) for i in range(200)]

        result = provider.get_matrix(origins, destinations)

        assert call_count > 1, "Should have made multiple chunked requests"
        assert len(result.durations_seconds) == 200
        assert len(result.durations_seconds[0]) == 200


def _make_mock_result(n_origins: int, n_destinations: int) -> Any:
    """Create a MatrixResult for testing chunking."""
    from routebench.infra.matrix.base import MatrixResult

    return MatrixResult(
        durations_seconds=[
            [100.0 * (i + j + 1) for j in range(n_destinations)]
            for i in range(n_origins)
        ],
        distances_meters=[
            [5000.0 * (i + j + 1) for j in range(n_destinations)]
            for i in range(n_origins)
        ],
        provider="osrm",
        cached=False,
    )
