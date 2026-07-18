"""A matrix provider that survives its backend going away."""

from __future__ import annotations

from datetime import datetime

import structlog

from routebench.core.exceptions import MatrixUnavailableError
from routebench.infra.matrix.base import MatrixProvider, MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class FallbackMatrixProvider:
    """Tries a primary provider, falls back to a secondary when it is unavailable.

    Composed the same way TrafficAdjustedProvider wraps a backend, so the three
    can stack: traffic banding over fallback over OSRM.

    Only MatrixUnavailableError is caught. That is the error OSRM raises when it
    is unreachable, times out, or answers with a service-level failure — the
    "the backend is gone" case this exists for. Anything else (a bug in our own
    request building, a coordinate that should never have passed validation)
    propagates, because silently substituting estimates for a real defect would
    hide it behind plausible-looking numbers.
    """

    name: str = "fallback"

    def __init__(self, primary: MatrixProvider, fallback: MatrixProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}_or_{fallback.name}"

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        """The primary's result, or the fallback's if the primary is unavailable."""
        try:
            return self.primary.get_matrix(
                origins,
                destinations,
                departure_time=departure_time,
                origin_departure_times=origin_departure_times,
            )
        except MatrixUnavailableError as exc:
            # Warning, not exception: this is a handled degradation, and the
            # result carries approximate=True so every downstream consumer —
            # including the grade, which is withheld — can see it happened.
            logger.warning(
                "matrix_provider_fell_back",
                primary=self.primary.name,
                fallback=self.fallback.name,
                reason=str(exc),
            )
            return self.fallback.get_matrix(
                origins,
                destinations,
                departure_time=departure_time,
                origin_departure_times=origin_departure_times,
            )
