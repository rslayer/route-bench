"""Cost guardrails — per-session and daily budget enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class BudgetTracker:
    """Tracks daily spend and enforces budget caps."""

    def __init__(self, daily_budget_usd: float = 50.0) -> None:
        self._daily_budget = daily_budget_usd
        self._daily_spend: dict[str, float] = {}
        self._budget_rejections: int = 0

    def _today_key(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def record_spend(self, amount_usd: float) -> None:
        key = self._today_key()
        self._daily_spend[key] = self._daily_spend.get(key, 0.0) + amount_usd

    def today_spend(self) -> float:
        return self._daily_spend.get(self._today_key(), 0.0)

    def is_exceeded(self) -> bool:
        exceeded = self.today_spend() >= self._daily_budget
        if exceeded:
            self._budget_rejections += 1
            logger.warning(
                "daily_budget_exceeded",
                budget=self._daily_budget,
                spend=self.today_spend(),
            )
        return exceeded

    @property
    def rejections(self) -> int:
        return self._budget_rejections

    @property
    def daily_budget(self) -> float:
        return self._daily_budget
