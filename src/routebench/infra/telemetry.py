"""Telemetry for RouteBench.

Records LLM calls, matrix calls, and solver calls with structured context.
Per-session totals are written to data/telemetry/{session_id}.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class LLMCallRecord(BaseModel):
    """Record of a single LLM API call."""

    model: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    slot_id: str | None = None
    turn_id: str | None = None
    timestamp: float = Field(default_factory=time.time)


class MatrixCallRecord(BaseModel):
    """Record of a single matrix API call."""

    provider: str
    n_origins: int
    n_destinations: int
    cached: bool
    latency_seconds: float
    timestamp: float = Field(default_factory=time.time)


class SolverCallRecord(BaseModel):
    """Record of a single solver call."""

    problem_type: str
    n_stops: int
    time_limit_seconds: float
    optimality_gap: float | None = None
    solve_time_seconds: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class Telemetry:
    """Session-level telemetry collector.

    Records every LLM call, matrix call, and solver call.
    Flushes to disk at the end of a session.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.llm_calls: list[LLMCallRecord] = []
        self.matrix_calls: list[MatrixCallRecord] = []
        self.solver_calls: list[SolverCallRecord] = []

    def record_llm_call(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_seconds: float,
        slot_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Record a single LLM API call."""
        record = LLMCallRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_seconds=latency_seconds,
            slot_id=slot_id,
            turn_id=turn_id,
        )
        self.llm_calls.append(record)
        logger.info(
            "llm_call_recorded",
            session_id=self.session_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_seconds=latency_seconds,
            slot_id=slot_id,
            turn_id=turn_id,
        )

    def record_matrix_call(
        self,
        *,
        provider: str,
        n_origins: int,
        n_destinations: int,
        cached: bool,
        latency_seconds: float,
    ) -> None:
        """Record a single matrix API call."""
        record = MatrixCallRecord(
            provider=provider,
            n_origins=n_origins,
            n_destinations=n_destinations,
            cached=cached,
            latency_seconds=latency_seconds,
        )
        self.matrix_calls.append(record)
        logger.info(
            "matrix_call_recorded",
            session_id=self.session_id,
            provider=provider,
            n_origins=n_origins,
            n_destinations=n_destinations,
            cached=cached,
            latency_seconds=latency_seconds,
        )

    def record_solver_call(
        self,
        *,
        problem_type: str,
        n_stops: int,
        time_limit_seconds: float,
        optimality_gap: float | None = None,
        solve_time_seconds: float = 0.0,
    ) -> None:
        """Record a single solver call."""
        record = SolverCallRecord(
            problem_type=problem_type,
            n_stops=n_stops,
            time_limit_seconds=time_limit_seconds,
            optimality_gap=optimality_gap,
            solve_time_seconds=solve_time_seconds,
        )
        self.solver_calls.append(record)
        logger.info(
            "solver_call_recorded",
            session_id=self.session_id,
            problem_type=problem_type,
            n_stops=n_stops,
            time_limit_seconds=time_limit_seconds,
            optimality_gap=optimality_gap,
            solve_time_seconds=solve_time_seconds,
        )

    def summary(self) -> dict[str, Any]:
        """Compute per-session summary totals."""
        total_llm_input = sum(c.input_tokens for c in self.llm_calls)
        total_llm_output = sum(c.output_tokens for c in self.llm_calls)
        total_llm_latency = sum(c.latency_seconds for c in self.llm_calls)
        total_matrix_latency = sum(c.latency_seconds for c in self.matrix_calls)
        total_solver_time = sum(c.solve_time_seconds for c in self.solver_calls)
        matrix_cached = sum(1 for c in self.matrix_calls if c.cached)

        return {
            "session_id": self.session_id,
            "llm": {
                "total_calls": len(self.llm_calls),
                "total_input_tokens": total_llm_input,
                "total_output_tokens": total_llm_output,
                "total_latency_seconds": round(total_llm_latency, 2),
            },
            "matrix": {
                "total_calls": len(self.matrix_calls),
                "cached_calls": matrix_cached,
                "total_latency_seconds": round(total_matrix_latency, 2),
            },
            "solver": {
                "total_calls": len(self.solver_calls),
                "total_solve_time_seconds": round(total_solver_time, 2),
            },
        }

    def flush_to_disk(self, base_dir: str | Path = "data/telemetry") -> Path:
        """Write session telemetry to disk.

        Returns the path to the written file.
        """
        out_dir = Path(base_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{self.session_id}.json"

        payload: dict[str, Any] = {
            "summary": self.summary(),
            "llm_calls": [c.model_dump() for c in self.llm_calls],
            "matrix_calls": [c.model_dump() for c in self.matrix_calls],
            "solver_calls": [c.model_dump() for c in self.solver_calls],
        }

        out_path.write_text(json.dumps(payload, indent=2))
        logger.info(
            "telemetry_flushed",
            session_id=self.session_id,
            path=str(out_path),
        )
        return out_path
