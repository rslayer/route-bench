"""Tests for SessionRegistry and session models."""

from __future__ import annotations

from pathlib import Path

import pytest

from routebench.app.sessions import (
    CostSummary,
    SessionRegistry,
    SessionStatus,
)
from routebench.infra.storage.local import LocalStorageBackend


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


@pytest.fixture()
def registry(storage: LocalStorageBackend) -> SessionRegistry:
    return SessionRegistry(storage=storage)


class TestSessionStatus:
    """Tests for SessionStatus model."""

    def test_create_default(self) -> None:
        status = SessionStatus(session_id="test-123")
        assert status.state == "queued"
        assert status.progress_pct == 0
        assert status.error is None
        assert status.artifacts is None

    def test_serialization(self) -> None:
        status = SessionStatus(
            session_id="test-123",
            state="succeeded",
            progress_pct=100,
            cost=CostSummary(input_tokens=1000, output_tokens=200),
        )
        json_str = status.model_dump_json()
        loaded = SessionStatus.model_validate_json(json_str)
        assert loaded.session_id == "test-123"
        assert loaded.state == "succeeded"
        assert loaded.cost is not None
        assert loaded.cost.input_tokens == 1000


class TestSessionRegistry:
    """Tests for SessionRegistry."""

    def test_create_and_get(self, registry: SessionRegistry) -> None:
        status = registry.create("sess-1")
        assert status.session_id == "sess-1"
        assert status.state == "queued"

    def test_update(self, registry: SessionRegistry) -> None:
        registry.create("sess-1")
        updated = registry.update("sess-1", state="analyzing", progress_pct=50)
        assert updated is not None
        assert updated.state == "analyzing"
        assert updated.progress_pct == 50

    def test_update_nonexistent(self, registry: SessionRegistry) -> None:
        result = registry.update("nonexistent", state="failed")
        assert result is None

    @pytest.mark.asyncio()
    async def test_persist_and_get_from_storage(self, registry: SessionRegistry) -> None:
        registry.create("sess-1")
        registry.update("sess-1", state="succeeded", progress_pct=100)
        await registry.persist("sess-1")
        registry.remove_active("sess-1")

        # Should load from storage
        status = await registry.get("sess-1")
        assert status is not None
        assert status.state == "succeeded"

    @pytest.mark.asyncio()
    async def test_get_nonexistent(self, registry: SessionRegistry) -> None:
        status = await registry.get("nonexistent")
        assert status is None

    def test_active_count(self, registry: SessionRegistry) -> None:
        assert registry.active_count == 0
        registry.create("sess-1")
        registry.create("sess-2")
        assert registry.active_count == 2
        registry.remove_active("sess-1")
        assert registry.active_count == 1
