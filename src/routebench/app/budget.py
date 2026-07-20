"""Cost guardrails — per-session and daily budget enforcement.

The daily total is held in an append-only JSONL ledger in storage rather than in
memory, so a restart or redeploy cannot silently reset the cap and hand the day a
second full budget.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import structlog

from routebench.infra.storage.base import StorageBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_LEDGER_PREFIX = "ledger"


def ledger_key(day: str, prefix: str = _LEDGER_PREFIX) -> str:
    """Storage key for a day's ledger under a given prefix."""
    return f"{prefix}/{day}.jsonl"


class BudgetTracker:
    """Tracks daily spend against a cap, backed by a durable ledger.

    One JSONL line per session. The check sums today's file at enqueue time,
    which costs one small read per upload and keeps the cap honest across
    restarts.
    """

    def __init__(
        self,
        storage: StorageBackend,
        daily_budget_usd: float = 50.0,
        ledger_prefix: str = _LEDGER_PREFIX,
    ) -> None:
        self._storage = storage
        self._daily_budget = daily_budget_usd
        # Distinct ledgers must not share a prefix, or two trackers (LLM spend
        # and matrix spend) would append to the same daily file and each would
        # read the other's spend as its own. The default keeps the LLM tracker's
        # historical key unchanged.
        self._ledger_prefix = ledger_prefix
        self._budget_rejections: int = 0
        # Appends are read-modify-write on S3, so serialize them in-process.
        self._append_lock = asyncio.Lock()

    def _today_key(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    async def record_spend(
        self,
        amount_usd: float,
        session_id: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Append one session's cost to today's ledger."""
        entry = {
            "session_id": session_id,
            "cost_usd": round(amount_usd, 6),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        line = (json.dumps(entry, separators=(",", ":")) + "\n").encode()
        key = ledger_key(self._today_key(), self._ledger_prefix)

        async with self._append_lock:
            try:
                await self._storage.append_object(key, line)
            except Exception:
                # A lost ledger line under-counts the day. Surface it rather than
                # letting spend drift silently upward.
                logger.exception("budget_ledger_write_failed", session_id=session_id, key=key)

    async def today_spend(self) -> float:
        """Sum today's ledger. Returns 0.0 when the day has no ledger yet."""
        key = ledger_key(self._today_key(), self._ledger_prefix)
        try:
            raw = await self._storage.read_object(key)
        except FileNotFoundError:
            return 0.0
        except Exception:
            logger.exception("budget_ledger_read_failed", key=key)
            return 0.0

        total = 0.0
        for line in raw.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # A torn final line must not take the budget check down with it.
                logger.warning("budget_ledger_bad_line", key=key)
                continue
            value = entry.get("cost_usd", 0.0)
            if isinstance(value, (int, float)):
                total += float(value)
        return total

    async def is_exceeded(self) -> bool:
        spend = await self.today_spend()
        exceeded = spend >= self._daily_budget
        if exceeded:
            self._budget_rejections += 1
            logger.warning(
                "daily_budget_exceeded",
                budget=self._daily_budget,
                spend=spend,
            )
        return exceeded

    @property
    def rejections(self) -> int:
        return self._budget_rejections

    @property
    def daily_budget(self) -> float:
        return self._daily_budget
