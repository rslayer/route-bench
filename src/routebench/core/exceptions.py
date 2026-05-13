"""Custom exceptions for RouteBench.

Every exception carries structured context (not just a string).
Catch at the pipeline boundary; do not catch broadly inside analysis tools.
"""

from __future__ import annotations

from typing import Any


class RouteBenchError(Exception):
    """Base exception for all RouteBench errors."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = context or {}


class InvalidInputError(RouteBenchError):
    """Schema or validation failure."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        row: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if field is not None:
            ctx["field"] = field
        if row is not None:
            ctx["row"] = row
        super().__init__(message, ctx)
        self.field = field
        self.row = row


class MatrixUnavailableError(RouteBenchError):
    """OSRM down or returned an error."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if provider is not None:
            ctx["provider"] = provider
        if status_code is not None:
            ctx["status_code"] = status_code
        super().__init__(message, ctx)
        self.provider = provider
        self.status_code = status_code


class SolverInfeasibleError(RouteBenchError):
    """OR-Tools could not find a feasible solution."""

    def __init__(
        self,
        message: str,
        *,
        problem_type: str | None = None,
        n_stops: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if problem_type is not None:
            ctx["problem_type"] = problem_type
        if n_stops is not None:
            ctx["n_stops"] = n_stops
        super().__init__(message, ctx)
        self.problem_type = problem_type
        self.n_stops = n_stops


class LLMError(RouteBenchError):
    """Claude API failure."""

    def __init__(
        self,
        message: str,
        *,
        model: str | None = None,
        slot_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if model is not None:
            ctx["model"] = model
        if slot_id is not None:
            ctx["slot_id"] = slot_id
        super().__init__(message, ctx)
        self.model = model
        self.slot_id = slot_id


class VerificationFailedError(RouteBenchError):
    """Verifier rejected output after retries."""

    def __init__(
        self,
        message: str,
        *,
        slot_id: str | None = None,
        failures: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if slot_id is not None:
            ctx["slot_id"] = slot_id
        if failures is not None:
            ctx["failures"] = failures
        super().__init__(message, ctx)
        self.slot_id = slot_id
        self.failures = failures or []


class BudgetExceededError(RouteBenchError):
    """Per-session or daily budget cap exceeded."""

    def __init__(
        self,
        message: str,
        *,
        budget_type: str = "session",
        limit: float = 0.0,
        current: float = 0.0,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        ctx["budget_type"] = budget_type
        ctx["limit"] = limit
        ctx["current"] = current
        super().__init__(message, ctx)
        self.budget_type = budget_type
        self.limit = limit
        self.current = current


class JobTimeoutError(RouteBenchError):
    """Session job exceeded the configured timeout."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        timeout_seconds: int = 0,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if session_id is not None:
            ctx["session_id"] = session_id
        ctx["timeout_seconds"] = timeout_seconds
        super().__init__(message, ctx)
        self.session_id = session_id
        self.timeout_seconds = timeout_seconds
