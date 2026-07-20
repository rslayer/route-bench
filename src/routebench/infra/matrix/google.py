"""Google Routes computeRouteMatrix MatrixProvider.

An alternative to OSRM that returns *live traffic-aware* travel times rather
than free-flow ones. It implements the same `MatrixProvider.get_matrix`
contract, so it slots into the fallback chain exactly where OSRM does — and on
any failure it raises `MatrixUnavailableError`, so the existing fallback catches
it and the run degrades to haversine estimates (grade withheld) rather than
erroring.

It is off unless `matrix_engine == "google"` because it BILLS PER ELEMENT
(origins x destinations). A single fleet benchmark is (stops + depot)² elements,
so this is real money per run — hence a deployment secret the operator opts into,
never a default and never a per-upload choice.

Docs: https://developers.google.com/maps/documentation/routes/compute_route_matrix
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import structlog

from routebench.core.exceptions import MatrixUnavailableError
from routebench.core.schemas import Fleet
from routebench.infra.matrix.base import MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_ENDPOINT = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

# Google caps a single computeRouteMatrix call at 625 elements, and separately at
# 50 origins and 50 destinations. Chunking at 25x25 = 625 satisfies all three at
# once, so every sub-request is the largest Google will accept.
_MAX_ORIGINS_PER_REQUEST = 25
_MAX_DESTINATIONS_PER_REQUEST = 25

# Advanced tier (required for TRAFFIC_AWARE) is roughly USD 10 per 1000 elements.
# Used to populate MatrixResult.cost_estimate and to estimate a run's spend for
# the daily matrix cap. Not a billing source of truth — the Google console is.
_USD_PER_ELEMENT = 0.01

_DEFAULT_TIMEOUT_SECONDS = 30.0

# Only these fields are returned, keeping the response small. `condition` and
# `status` distinguish "no route" and per-element errors from a real duration.
_FIELD_MASK = "originIndex,destinationIndex,duration,distanceMeters,condition,status"


class GoogleMatrixProvider:
    """Traffic-aware matrix provider backed by Google Routes computeRouteMatrix."""

    name: str = "google"
    # Live traffic: the answer depends on the departure time, so callers should
    # pass the plan's start time rather than defaulting to "now".
    is_time_aware: bool = True

    def __init__(
        self,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            # A misconfigured engine should fail loudly at construction, not
            # silently 403 on the first matrix call mid-analysis.
            raise ValueError("GoogleMatrixProvider requires a non-empty api_key")
        self._api_key = api_key
        self._timeout = timeout

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        """Fetch a traffic-aware origin-destination matrix from Google.

        `departure_time` (or, failing that, the earliest `origin_departure_times`)
        chooses the traffic conditions. The analysis threads the plan's start
        time in here because this provider is time-aware, so a plan is graded
        against the traffic it would actually face rather than "now". Google
        predicts only forward, so a time in the past is rolled forward whole
        weeks — preserving day-of-week and time-of-day — so benchmarking last
        Tuesday's 8am plan uses next Tuesday's 8am traffic rather than being
        rejected. With neither supplied it falls back to the current time.

        Raises MatrixUnavailableError on any failure so the fallback engages.
        """
        depart = self._resolve_departure(departure_time, origin_departure_times)

        n_o = len(origins)
        n_d = len(destinations)
        durations = [[float("inf")] * n_d for _ in range(n_o)]
        distances = [[float("inf")] * n_d for _ in range(n_o)]

        for o_start in range(0, n_o, _MAX_ORIGINS_PER_REQUEST):
            o_chunk = origins[o_start : o_start + _MAX_ORIGINS_PER_REQUEST]
            for d_start in range(0, n_d, _MAX_DESTINATIONS_PER_REQUEST):
                d_chunk = destinations[d_start : d_start + _MAX_DESTINATIONS_PER_REQUEST]
                elements = self._request_chunk(o_chunk, d_chunk, depart)
                # Elements can arrive out of order and can be missing entirely
                # for an unroutable pair, so place each by its own indices and
                # leave the rest at the inf they were initialised to.
                for el in elements:
                    i = el.get("originIndex")
                    j = el.get("destinationIndex")
                    if not isinstance(i, int) or not isinstance(j, int):
                        continue
                    dur, dist = _parse_element(el)
                    durations[o_start + i][d_start + j] = dur
                    distances[o_start + i][d_start + j] = dist

        n_elements = n_o * n_d
        logger.info(
            "google_matrix_fetched",
            n_origins=n_o,
            n_destinations=n_d,
            n_elements=n_elements,
            departure_time=depart.isoformat(),
        )
        return MatrixResult(
            durations_seconds=durations,
            distances_meters=distances,
            provider="google",
            cached=False,
            cost_estimate=round(n_elements * _USD_PER_ELEMENT, 4),
            approximate=False,
        )

    def _resolve_departure(
        self,
        departure_time: datetime | None,
        origin_departure_times: list[datetime] | None,
    ) -> datetime:
        chosen = departure_time
        if chosen is None and origin_departure_times:
            chosen = min(origin_departure_times)
        if chosen is None:
            chosen = datetime.now(UTC)
        # Naive datetimes are assumed UTC rather than rejected — planned start
        # times upstream are wall-clock and may arrive tz-naive.
        if chosen.tzinfo is None:
            chosen = chosen.replace(tzinfo=UTC)
        # Google rejects a past departureTime for traffic-aware routing. Roll
        # forward by whole weeks so the weekday and time-of-day — what traffic
        # actually depends on — are preserved.
        now = datetime.now(UTC)
        while chosen <= now + timedelta(minutes=1):
            chosen = chosen + timedelta(weeks=1)
        return chosen

    def _request_chunk(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        depart: datetime,
    ) -> list[dict[str, object]]:
        body = {
            "origins": [_waypoint(lat, lon) for lat, lon in origins],
            "destinations": [_waypoint(lat, lon) for lat, lon in destinations],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "departureTime": depart.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        try:
            response = httpx.post(_ENDPOINT, json=body, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise MatrixUnavailableError(
                f"Google matrix request timed out after {self._timeout}s",
                provider="google",
                context={"error": str(exc)},
            ) from exc
        except httpx.HTTPError as exc:
            raise MatrixUnavailableError(
                "Could not reach the Google Routes API",
                provider="google",
                context={"error": str(exc)},
            ) from exc

        if response.status_code != 200:
            # The key, the billing account, or the request is wrong. Surface a
            # little of the body — never the key — so the operator can diagnose.
            raise MatrixUnavailableError(
                f"Google Routes API returned status {response.status_code}",
                provider="google",
                status_code=response.status_code,
                context={"body": response.text[:500]},
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise MatrixUnavailableError(
                "Google Routes API returned a non-JSON body",
                provider="google",
                context={"body": response.text[:500]},
            ) from exc

        if not isinstance(data, list):
            raise MatrixUnavailableError(
                "Google Routes API returned an unexpected shape (expected a list of elements)",
                provider="google",
                context={"body": str(data)[:500]},
            )
        return data


def _waypoint(lat: float, lon: float) -> dict[str, object]:
    return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lon}}}}


def _parse_element(el: dict[str, object]) -> tuple[float, float]:
    """Extract (duration_seconds, distance_meters) from one matrix element.

    A pair with no route, or a per-element error status, becomes (inf, inf) —
    the same "unreachable" sentinel OSRM's null produces, which the solvers and
    metrics already handle.
    """
    # A non-empty `status` is a google.rpc.Status describing a per-element error.
    status = el.get("status")
    if isinstance(status, dict) and status.get("code", 0) not in (0, None):
        return float("inf"), float("inf")
    if el.get("condition") == "ROUTE_NOT_FOUND":
        return float("inf"), float("inf")

    raw_duration = el.get("duration")
    distance = el.get("distanceMeters", 0)
    if raw_duration is None:
        return float("inf"), float("inf")
    # Durations come back as protobuf duration strings like "412s"; distance is
    # a number. Both arrive typed as `object` off the JSON, so coerce via str()
    # inside the guard — a malformed value falls through to the unreachable
    # sentinel rather than raising into the matrix build.
    try:
        seconds = float(str(raw_duration).rstrip("s"))
        meters = float(str(distance)) if distance is not None else 0.0
    except (TypeError, ValueError):
        return float("inf"), float("inf")
    return seconds, meters


def estimate_run_cost_usd(fleet: Fleet, include_benchmark: bool) -> float:
    """Upfront, deterministic estimate of one run's Google matrix spend, in USD.

    Google bills per element (origins x destinations). A run fetches one square
    matrix per route for scoring and the route benchmark — (stops + depot)^2 —
    and, when the fleet benchmark runs, one combined matrix over every stop —
    (total stops + depot)^2. This is the no-cache worst case, which is exactly
    what a spend cap should meter: it never under-counts, so it cannot let the
    bill run past the cap by trusting cache hits that might not happen.
    """
    total_stops = 0
    elements = 0
    for route in fleet.routes:
        n = len(route.stops) + 1  # + depot
        elements += n * n
        total_stops += len(route.stops)
    if include_benchmark:
        fleet_n = total_stops + 1
        elements += fleet_n * fleet_n
    return round(elements * _USD_PER_ELEMENT, 4)
