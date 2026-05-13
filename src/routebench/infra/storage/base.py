"""StorageBackend protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Async storage abstraction for session artifacts.

    Layout: sessions/{session_id}/{filename}
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

    async def delete_session(self, session_id: str) -> None:
        """Delete all artifacts for a session."""
        ...

    async def list_sessions(self) -> list[str]:
        """List all session IDs in storage."""
        ...

    async def is_writable(self) -> bool:
        """Check that storage is writable (for health checks)."""
        ...
