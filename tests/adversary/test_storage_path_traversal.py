"""Defect: LocalStorageBackend.read_object/append_object do not confine
`key` to the configured base path.

Impact: arbitrary file read (`read_object`) and arbitrary file write/append
(`append_object`) anywhere on the filesystem the process can reach, if a
caller ever passes an attacker-influenced key. This is a path-traversal /
arbitrary-file primitive in the storage layer itself.

Root cause: `_object_path` joins the caller-supplied key onto the base path
with plain `Path.__truediv__`:

    def _object_path(self, key: str) -> Path:
        return self._base / _OBJECTS_DIR / key

`pathlib` gives `Path.__truediv__` POSIX join semantics: if the right-hand
operand is an ABSOLUTE path, it completely REPLACES the left-hand side
rather than being appended to it. So a key of "/etc/passwd" resolves to
`Path("/etc/passwd")`, discarding `self._base` and `_OBJECTS_DIR` entirely.
A relative key containing "../" segments has the ordinary traversal effect
of escaping `_objects/` (and potentially `base_path` itself).

Today's only caller (`BudgetTracker`) builds keys internally from a
`YYYY-MM-DD` string, so this is not yet reachable from an HTTP request —
but the storage backend provides no defense in depth, and any future
caller that derives a key from user input (a session id, a filename) would
turn this into a directly exploitable arbitrary file read/write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from routebench.infra.storage.local import LocalStorageBackend


@pytest.mark.asyncio()
async def test_absolute_key_escapes_base_path_on_write(
    storage: LocalStorageBackend, tmp_path: Path
) -> None:
    """An absolute-path key must not let append_object write outside base_path."""
    target = tmp_path / "escaped_write.txt"
    assert not target.exists()

    await storage.append_object(str(target), b"pwned")

    assert not target.exists(), (
        f"append_object() wrote outside its configured base_path to {target}; "
        "an absolute-path key silently overrides Path.__truediv__ join semantics."
    )


@pytest.mark.asyncio()
async def test_absolute_key_escapes_base_path_on_read(
    storage: LocalStorageBackend, tmp_path: Path
) -> None:
    """An absolute-path key must not let read_object read arbitrary files."""
    secret = tmp_path / "secret_outside_base.txt"
    secret.write_bytes(b"top secret, not a session object")

    with pytest.raises(FileNotFoundError):
        # A correctly-confined backend has no object at this key (it would
        # look for {base}/_objects/{tmp_path}/secret_outside_base.txt, which
        # does not exist). The defect lets it read the real file instead.
        await storage.read_object(str(secret))


@pytest.mark.asyncio()
async def test_dotdot_key_escapes_objects_namespace(
    storage: LocalStorageBackend, tmp_path: Path
) -> None:
    """A "../"-shaped relative key must not escape the _objects/ namespace."""
    traversal_key = "../../outside_objects.txt"

    await storage.append_object(traversal_key, b"escaped via ../")

    escaped_path = (tmp_path / "outside_objects.txt").resolve()
    assert not escaped_path.exists(), (
        f"append_object() with key={traversal_key!r} escaped the _objects/ "
        f"namespace and wrote to {escaped_path}"
    )
