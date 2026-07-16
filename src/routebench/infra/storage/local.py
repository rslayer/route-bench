"""Local filesystem StorageBackend implementation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


# Non-session objects live under a reserved directory inside the configured
# base, rather than a sibling of it: writing outside the operator's configured
# storage path would be a surprise. Session IDs are uuid4 hex and can never
# start with "_", so the namespaces cannot collide.
_OBJECTS_DIR = "_objects"


class LocalStorageBackend:
    """Stores session artifacts on the local filesystem.

    Layout: {base_path}/{session_id}/{filename}
    Non-session objects: {base_path}/_objects/{key}
    All filesystem I/O is offloaded to a thread via asyncio.to_thread().
    """

    def __init__(self, base_path: str = "./data/sessions") -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self._base / session_id

    def _object_path(self, key: str) -> Path:
        return self._base / _OBJECTS_DIR / key

    async def write(self, session_id: str, filename: str, data: bytes) -> None:
        def _write() -> None:
            d = self._session_dir(session_id)
            d.mkdir(parents=True, exist_ok=True)
            (d / filename).write_bytes(data)

        await asyncio.to_thread(_write)

    async def read(self, session_id: str, filename: str) -> bytes:
        def _read() -> bytes:
            p = self._session_dir(session_id) / filename
            if not p.exists():
                msg = f"File not found: {session_id}/{filename}"
                raise FileNotFoundError(msg)
            return p.read_bytes()

        return await asyncio.to_thread(_read)

    async def exists(self, session_id: str, filename: str) -> bool:
        return await asyncio.to_thread(lambda: (self._session_dir(session_id) / filename).exists())

    async def presigned_url(self, session_id: str, filename: str, ttl_seconds: int = 900) -> str:
        return f"/sessions/{session_id}/{filename}"

    async def delete_session(self, session_id: str) -> None:
        def _delete() -> None:
            d = self._session_dir(session_id)
            if d.exists():
                shutil.rmtree(d)
                logger.info("session_deleted", session_id=session_id)

        await asyncio.to_thread(_delete)

    async def list_sessions(self) -> list[str]:
        def _list() -> list[str]:
            if not self._base.exists():
                return []
            return [
                p.name for p in self._base.iterdir() if p.is_dir() and not p.name.startswith("_")
            ]

        return await asyncio.to_thread(_list)

    async def read_object(self, key: str) -> bytes:
        def _read() -> bytes:
            p = self._object_path(key)
            if not p.exists():
                msg = f"Object not found: {key}"
                raise FileNotFoundError(msg)
            return p.read_bytes()

        return await asyncio.to_thread(_read)

    async def append_object(self, key: str, data: bytes) -> None:
        """Append to an object. The local filesystem appends natively."""

        def _append() -> None:
            p = self._object_path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("ab") as f:
                f.write(data)

        await asyncio.to_thread(_append)

    async def is_writable(self) -> bool:
        def _check() -> bool:
            try:
                probe = self._base / ".probe"
                probe.write_text("ok")
                probe.unlink()
                return True
            except OSError:
                return False

        return await asyncio.to_thread(_check)
