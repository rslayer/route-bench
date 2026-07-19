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

    def _confine(self, root: Path, parts: tuple[str, ...], what: str) -> Path:
        """Resolve `parts` under `root`, or refuse.

        `root / part` is not safe on its own: pathlib gives `/` POSIX join
        semantics, so an ABSOLUTE part silently replaces the root entirely
        ("/etc/passwd" resolves to /etc/passwd, not root/etc/passwd), and "../"
        segments walk out of it.

        Confinement is checked after resolution rather than by scrubbing "..":
        blacklisting patterns invites the next encoding trick, while asking
        "did we land inside the directory?" is the actual question. Resolution
        also collapses symlinks, so a symlinked session directory cannot point
        out either.
        """
        resolved_root = root.resolve()
        candidate = resolved_root.joinpath(*parts).resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            msg = f"{what} escapes the storage root: {'/'.join(parts)!r}"
            raise ValueError(msg)
        return candidate

    def _session_dir(self, session_id: str) -> Path:
        """Confine the session directory.

        This was a bare `self._base / session_id`. Run 1 of the robustness
        harness found the identical flaw in `_object_path` and it was fixed
        there — but only there, so the door next to it stayed open, and this is
        the one every session route actually goes through. Run 3 walked in:
        `GET /sessions/%2e%2e/report.html` returned 200 with a file from
        outside the root. A literal ".." is normalised away by the HTTP client
        before it is ever sent, which is most likely why this looked fixed;
        "%2e%2e" travels intact and decodes server-side.

        `delete_session` is the sharper end of the same hole — it rmtree's
        whatever this returns, so a traversing id deleted an arbitrary
        directory outside the storage root.
        """
        return self._confine(self._base, (session_id,), "Session id")

    def _session_file(self, session_id: str, filename: str) -> Path:
        """Confine both halves of a session artifact path.

        `filename` is caller-supplied too, and confining only the directory
        would leave `read(session_id, "../../etc/passwd")` open — today it
        fails only because the session directory usually does not exist yet,
        which is luck, not a guarantee.
        """
        return self._confine(self._base, (session_id, filename), "Session path")

    def _object_path(self, key: str) -> Path:
        """Resolve an object key inside the objects directory, or refuse.

        Today every key is built internally (BudgetTracker's date-formatted
        ledger name), so this is not reachable — which is exactly why it is
        worth closing now, while it is cheap and before some future caller
        forwards a key from a request.
        """
        return self._confine(self._base / _OBJECTS_DIR, (key,), "Object key")

    async def write(self, session_id: str, filename: str, data: bytes) -> None:
        def _write() -> None:
            p = self._session_file(session_id, filename)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

        await asyncio.to_thread(_write)

    async def read(self, session_id: str, filename: str) -> bytes:
        def _read() -> bytes:
            p = self._session_file(session_id, filename)
            if not p.exists():
                msg = f"File not found: {session_id}/{filename}"
                raise FileNotFoundError(msg)
            return p.read_bytes()

        return await asyncio.to_thread(_read)

    async def exists(self, session_id: str, filename: str) -> bool:
        """A path that escapes the root does not exist, by definition.

        Deliberately False rather than a raised ValueError: every artifact
        route asks `exists()` first and 404s on False, so returning False turns
        a traversal attempt into the 404 it deserves. Letting the ValueError
        out would make `GET /sessions/%2e%2e/report.html` a 500 — an uncaught
        exception escaping the handler, which is the same shape of bug as the
        negative service_time crash from run 2, and it would tell the caller
        their path was interesting.
        """

        def _exists() -> bool:
            try:
                return self._session_file(session_id, filename).exists()
            except ValueError:
                logger.warning(
                    "storage_path_escape_rejected",
                    session_id=session_id,
                    filename=filename,
                )
                return False

        return await asyncio.to_thread(_exists)

    async def presigned_url(self, session_id: str, filename: str, ttl_seconds: int = 900) -> str:
        return f"/sessions/{session_id}/{filename}"

    async def delete_file(self, session_id: str, filename: str) -> bool:
        def _delete_file() -> bool:
            try:
                p = self._session_file(session_id, filename)
            except ValueError:
                # Same reasoning as `exists`: a traversing path names nothing we
                # own, so there is nothing to delete. Refusing quietly beats
                # letting a ValueError escape the retention loop and abort the
                # sweep for every session after this one.
                logger.warning(
                    "storage_path_escape_rejected",
                    session_id=session_id,
                    filename=filename,
                )
                return False
            if not p.exists():
                return False
            p.unlink()
            return True

        return await asyncio.to_thread(_delete_file)

    async def delete_session(self, session_id: str) -> None:
        def _delete() -> None:
            # Confined before rmtree, not after: unguarded, a traversing id
            # deleted an arbitrary directory outside the storage root. Refusing
            # loudly here rather than returning quietly — nothing should ever
            # ask this to delete something it cannot name.
            d = self._session_dir(session_id)
            if d == self._base.resolve():
                msg = f"Refusing to delete the storage root itself: {session_id!r}"
                raise ValueError(msg)
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
