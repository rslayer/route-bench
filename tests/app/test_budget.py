"""Tests for budget tracking and cost guardrails."""

from __future__ import annotations

from routebench.app.budget import BudgetTracker


class TestBudgetTracker:
    """Tests for BudgetTracker."""

    def test_initial_state(self) -> None:
        bt = BudgetTracker(daily_budget_usd=10.0)
        assert bt.today_spend() == 0.0
        assert bt.is_exceeded() is False

    def test_record_spend(self) -> None:
        bt = BudgetTracker(daily_budget_usd=10.0)
        bt.record_spend(3.0)
        assert bt.today_spend() == 3.0
        bt.record_spend(2.0)
        assert bt.today_spend() == 5.0

    def test_budget_exceeded(self) -> None:
        bt = BudgetTracker(daily_budget_usd=5.0)
        bt.record_spend(6.0)
        assert bt.is_exceeded() is True
        assert bt.rejections >= 1

    def test_budget_not_exceeded(self) -> None:
        bt = BudgetTracker(daily_budget_usd=10.0)
        bt.record_spend(3.0)
        assert bt.is_exceeded() is False
