"""StorageBackend protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Async storage abstraction for session artifacts.

    Layout: sessions/{session_id}/{filename}

    Alongside per-session files, backends store a small number of objects that
    belong to the deployment rather than any one session (the budget ledger).
    Those use `read_object`/`append_object` with an explicit key, keeping them
    out of the session namespace so `list_sessions` never sees them.
    """

    async def write(self, session_id: str, filename: str, data: bytes) -> None:
        """Write data to storage."""
        ...

    async def read(self, session_id: str, filename: str) -> bytes:
        """Read data from storage. Raises FileNotFoundError if missing."""
        ...

    async def exists(self, session_id: str, filename: str) -> bool:
        """Check if a file exists."""
        ...

    async def presigned_url(self, session_id: str, filename: str, ttl_seconds: int = 900) -> str:
        """Generate a pre-signed GET URL. For local backend, returns a relative path."""
        ...

    async def delete_file(self, session_id: str, filename: str) -> bool:
        """Delete a single artifact. Returns True if it existed and was removed.

        Distinct from `delete_session` because retention expires a session in two
        stages: the heavy artifacts (and the raw upload) go at the session TTL,
        while status and telemetry survive to the longer telemetry TTL so the
        session can still answer "this existed and expired" rather than 404ing.

        Missing is not an error — retention runs repeatedly over the same
        sessions and must be idempotent.
        """
        ...

    async def delete_session(self, session_id: str) -> None:
        """Delete all artifacts for a session."""
        ...

    async def list_sessions(self) -> list[str]:
        """List all session IDs in storage."""
        ...

    async def is_writable(self) -> bool:
        """Check that storage is writable (for health checks)."""
        ...

    async def read_object(self, key: str) -> bytes:
        """Read a non-session object by key. Raises FileNotFoundError if missing."""
        ...

    async def append_object(self, key: str, data: bytes) -> None:
        """Append bytes to a non-session object, creating it if absent.

        Object stores have no native append, so a backend may implement this as
        read-modify-write. Callers must serialize concurrent appends to the same
        key; single-machine deployments do this with a lock.
        """
        ...
