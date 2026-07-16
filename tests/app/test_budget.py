"""Tests for budget tracking and cost guardrails.

The daily total is ledger-backed now; tests/app/test_durability.py covers the
restart-survival cases, and these cover the in-cycle arithmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from routebench.app.budget import BudgetTracker
from routebench.infra.storage.local import LocalStorageBackend


@pytest.fixture
def tracker(tmp_path: Path) -> BudgetTracker:
    storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
    return BudgetTracker(storage=storage, daily_budget_usd=10.0)


class TestBudgetTracker:
    """Tests for BudgetTracker."""

    @pytest.mark.asyncio()
    async def test_initial_state(self, tracker: BudgetTracker) -> None:
        assert await tracker.today_spend() == 0.0
        assert await tracker.is_exceeded() is False

    @pytest.mark.asyncio()
    async def test_record_spend(self, tracker: BudgetTracker) -> None:
        await tracker.record_spend(3.0)
        assert await tracker.today_spend() == pytest.approx(3.0)
        await tracker.record_spend(2.0)
        assert await tracker.today_spend() == pytest.approx(5.0)

    @pytest.mark.asyncio()
    async def test_budget_exceeded(self, tmp_path: Path) -> None:
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        bt = BudgetTracker(storage=storage, daily_budget_usd=5.0)
        await bt.record_spend(6.0)
        assert await bt.is_exceeded() is True
        assert bt.rejections >= 1

    @pytest.mark.asyncio()
    async def test_budget_not_exceeded(self, tracker: BudgetTracker) -> None:
        await tracker.record_spend(3.0)
        assert await tracker.is_exceeded() is False

    @pytest.mark.asyncio()
    async def test_exactly_at_cap_is_exceeded(self, tmp_path: Path) -> None:
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        bt = BudgetTracker(storage=storage, daily_budget_usd=5.0)
        await bt.record_spend(5.0)
        assert await bt.is_exceeded() is True

    @pytest.mark.asyncio()
    async def test_daily_budget_exposed(self, tracker: BudgetTracker) -> None:
        assert tracker.daily_budget == 10.0
