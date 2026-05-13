"""Telemetry sink — persists session telemetry to storage and tracks aggregates."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import structlog

from routebench.infra.storage.base import StorageBackend
from routebench.infra.telemetry import Telemetry

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Claude pricing (per 1M tokens, as of 2025)
_CLAUDE_INPUT_PRICE_PER_M = 3.0
_CLAUDE_OUTPUT_PRICE_PER_M = 15.0


class TelemetrySink:
    """Collects session telemetry and writes it to storage.

    Also tracks aggregate counters for cost dashboards.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage
        self._daily_spend: dict[str, float] = {}  # date_str -> usd
        self._session_costs: list[dict[str, float | str]] = []

    async def flush(self, session_id: str, telemetry: Telemetry) -> float:
        """Flush telemetry for a session. Returns computed USD cost."""
        summary = telemetry.summary()

        # Compute cost
        llm = summary.get("llm", {})
        input_tokens = int(llm.get("total_input_tokens", 0))
        output_tokens = int(llm.get("total_output_tokens", 0))
        input_cost = input_tokens * _CLAUDE_INPUT_PRICE_PER_M / 1_000_000
        output_cost = output_tokens * _CLAUDE_OUTPUT_PRICE_PER_M / 1_000_000
        total_cost = input_cost + output_cost

        # Enrich summary
        summary["computed_cost_usd"] = round(total_cost, 6)
        summary["session_id"] = session_id
        summary["flushed_at"] = datetime.now(UTC).isoformat()

        # Write to storage
        data = json.dumps(summary, indent=2, default=str).encode()
        await self._storage.write(session_id, "telemetry.json", data)

        # Update aggregates
        date_key = datetime.now(UTC).strftime("%Y-%m-%d")
        self._daily_spend[date_key] = self._daily_spend.get(date_key, 0.0) + total_cost

        self._session_costs.append(
            {
                "session_id": session_id,
                "cost_usd": round(total_cost, 6),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "timestamp": time.time(),
            }
        )

        logger.info(
            "telemetry_flushed",
            session_id=session_id,
            cost_usd=round(total_cost, 6),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return total_cost

    def daily_spend(self, date_str: str | None = None) -> float:
        """Get total spend for a date (defaults to today)."""
        if date_str is None:
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self._daily_spend.get(date_str, 0.0)

    def cost_distribution(self, window_hours: int = 24) -> dict[str, float]:
        """Get cost distribution stats for recent sessions."""
        cutoff = time.time() - window_hours * 3600
        recent_costs = [
            float(c["cost_usd"]) for c in self._session_costs if float(str(c["timestamp"])) > cutoff
        ]
        if not recent_costs:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "total": 0.0}
        recent_costs.sort()
        n = len(recent_costs)
        return {
            "count": float(n),
            "p50": recent_costs[n // 2],
            "p95": recent_costs[int(n * 0.95)] if n > 1 else recent_costs[0],
            "max": recent_costs[-1],
            "total": sum(recent_costs),
        }
