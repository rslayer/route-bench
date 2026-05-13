"""Tests for LocalStorageBackend."""

from __future__ import annotations

from pathlib import Path

import pytest

from routebench.infra.storage.local import LocalStorageBackend


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


class TestLocalStorageBackend:
    """Tests for local filesystem storage."""

    @pytest.mark.asyncio()
    async def test_write_and_read(self, storage: LocalStorageBackend) -> None:
        await storage.write("sess-1", "test.txt", b"hello")
        data = await storage.read("sess-1", "test.txt")
        assert data == b"hello"

    @pytest.mark.asyncio()
    async def test_read_missing_raises(self, storage: LocalStorageBackend) -> None:
        with pytest.raises(FileNotFoundError):
            await storage.read("nonexistent", "test.txt")

    @pytest.mark.asyncio()
    async def test_exists(self, storage: LocalStorageBackend) -> None:
        assert await storage.exists("sess-1", "test.txt") is False
        await storage.write("sess-1", "test.txt", b"data")
        assert await storage.exists("sess-1", "test.txt") is True

    @pytest.mark.asyncio()
    async def test_presigned_url(self, storage: LocalStorageBackend) -> None:
        url = await storage.presigned_url("sess-1", "report.html")
        assert url == "/sessions/sess-1/report.html"

    @pytest.mark.asyncio()
    async def test_delete_session(self, storage: LocalStorageBackend) -> None:
        await storage.write("sess-del", "a.txt", b"a")
        await storage.write("sess-del", "b.txt", b"b")
        assert await storage.exists("sess-del", "a.txt") is True

        await storage.delete_session("sess-del")
        assert await storage.exists("sess-del", "a.txt") is False

    @pytest.mark.asyncio()
    async def test_list_sessions(self, storage: LocalStorageBackend) -> None:
        await storage.write("aaa", "test.txt", b"data")
        await storage.write("bbb", "test.txt", b"data")
        sessions = await storage.list_sessions()
        assert "aaa" in sessions
        assert "bbb" in sessions

    @pytest.mark.asyncio()
    async def test_is_writable(self, storage: LocalStorageBackend) -> None:
        assert await storage.is_writable() is True
