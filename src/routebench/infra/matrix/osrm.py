"""OSRM-backed MatrixProvider implementation.

Calls /table/v1/driving/{coords}?annotations=duration,distance against a
local or remote OSRM instance. For large matrices (>10K cells), chunks
the request into sub-matrices and stitches results.

Known v1 limitation: departure_time is accepted but ignored — OSRM does
not natively support time-of-day routing. The parameter is preserved for
interface compatibility with future providers.
"""

from __future__ import annotations

import math
from datetime import datetime

import httpx
import structlog

from routebench.core.exceptions import MatrixUnavailableError
from routebench.infra.matrix.base import MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# OSRM typically handles ~100x100 in a single request well.
# 10K cells = 100x100; chunk larger requests.
MAX_CELLS_PER_REQUEST = 10_000
DEFAULT_TIMEOUT_SECONDS = 10.0


class OSRMMatrixProvider:
    """Matrix provider backed by a self-hosted OSRM instance."""

    name: str = "osrm"

    def __init__(
        self,
        host: str = "http://localhost:5000",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        """Query OSRM for an origin-destination matrix.

        Args:
            origins: List of (lat, lon) tuples.
            destinations: List of (lat, lon) tuples.
            departure_time: Accepted but ignored in v1 (OSRM limitation).
            origin_departure_times: Accepted but ignored — OSRM returns free-flow
                times. Wrap this provider in TrafficAdjustedProvider to apply
                time-of-day banding.

        Returns:
            MatrixResult with durations (seconds) and distances (meters).

        Raises:
            MatrixUnavailableError: If OSRM is unreachable or returns an error.
        """
        n_origins = len(origins)
        n_destinations = len(destinations)
        total_cells = n_origins * n_destinations

        if total_cells <= MAX_CELLS_PER_REQUEST:
            return self._single_request(origins, destinations)

        return self._chunked_request(origins, destinations)

    def _single_request(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
    ) -> MatrixResult:
        """Make a single OSRM table request."""
        all_coords = origins + destinations
        coords_str = ";".join(f"{lon},{lat}" for lat, lon in all_coords)

        origin_indices = list(range(len(origins)))
        dest_indices = list(range(len(origins), len(origins) + len(destinations)))

        sources_param = ";".join(str(i) for i in origin_indices)
        destinations_param = ";".join(str(i) for i in dest_indices)

        url = (
            f"{self.host}/table/v1/driving/{coords_str}"
            f"?annotations=duration,distance"
            f"&sources={sources_param}"
            f"&destinations={destinations_param}"
        )

        try:
            response = httpx.get(url, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise MatrixUnavailableError(
                f"OSRM request timed out after {self.timeout}s",
                provider="osrm",
                context={"url": url, "error": str(exc)},
            ) from exc
        except httpx.ConnectError as exc:
            raise MatrixUnavailableError(
                f"Could not connect to OSRM at {self.host}",
                provider="osrm",
                context={"host": self.host, "error": str(exc)},
            ) from exc

        if response.status_code != 200:
            raise MatrixUnavailableError(
                f"OSRM returned status {response.status_code}",
                provider="osrm",
                status_code=response.status_code,
                context={"url": url, "body": response.text[:500]},
            )

        data = response.json()
        if data.get("code") != "Ok":
            raise MatrixUnavailableError(
                f"OSRM error: {data.get('code')} — {data.get('message', '')}",
                provider="osrm",
                context={"osrm_code": data.get("code"), "message": data.get("message")},
            )

        durations: list[list[float]] = data["durations"]
        distances: list[list[float]] = data["distances"]

        # Replace None values with infinity (OSRM returns null for unreachable)
        durations = [[float("inf") if v is None else float(v) for v in row] for row in durations]
        distances = [[float("inf") if v is None else float(v) for v in row] for row in distances]

        logger.info(
            "osrm_matrix_fetched",
            n_origins=len(origins),
            n_destinations=len(destinations),
        )

        return MatrixResult(
            durations_seconds=durations,
            distances_meters=distances,
            provider="osrm",
            cached=False,
        )

    def _chunked_request(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
    ) -> MatrixResult:
        """Split a large matrix request into chunks and stitch results."""
        n_origins = len(origins)
        n_destinations = len(destinations)

        # Determine chunk sizes: keep destination chunks fixed, split origins
        chunk_size = max(1, int(math.sqrt(MAX_CELLS_PER_REQUEST)))
        origin_chunks = [origins[i : i + chunk_size] for i in range(0, n_origins, chunk_size)]
        dest_chunks = [
            destinations[i : i + chunk_size] for i in range(0, n_destinations, chunk_size)
        ]

        # Initialize full result matrices
        full_durations: list[list[float]] = [[0.0] * n_destinations for _ in range(n_origins)]
        full_distances: list[list[float]] = [[0.0] * n_destinations for _ in range(n_origins)]

        origin_offset = 0
        for o_chunk in origin_chunks:
            dest_offset = 0
            for d_chunk in dest_chunks:
                result = self._single_request(o_chunk, d_chunk)

                for i, row_dur in enumerate(result.durations_seconds):
                    for j, val in enumerate(row_dur):
                        full_durations[origin_offset + i][dest_offset + j] = val

                for i, row_dist in enumerate(result.distances_meters):
                    for j, val in enumerate(row_dist):
                        full_distances[origin_offset + i][dest_offset + j] = val

                dest_offset += len(d_chunk)
            origin_offset += len(o_chunk)

        logger.info(
            "osrm_chunked_matrix_fetched",
            n_origins=n_origins,
            n_destinations=n_destinations,
            n_chunks=len(origin_chunks) * len(dest_chunks),
        )

        return MatrixResult(
            durations_seconds=full_durations,
            distances_meters=full_distances,
            provider="osrm",
            cached=False,
        )
