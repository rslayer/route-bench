"""Path traversal guards for LocalStorageBackend.

Promoted from robustness run 3, which found the `session_id` read vector via
`GET /sessions/%2e%2e/report.html`. The rest of these cover the same root cause
through doors the harness did not try — `filename`, absolute ids, and
`delete_session`, which is the sharpest of them because it rmtree's whatever it
is handed.

The root cause is one line of pathlib semantics: `base / part` lets an absolute
part replace the base outright and lets "../" walk out. Run 1 found exactly this
in `_object_path` and it was fixed there and only there, which is why run 3
found it still open next door. See ROBUSTNESS.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from routebench.app.api.app import create_app
from routebench.app.sessions import SessionRegistry
from routebench.core.config import Settings
from routebench.infra.storage.local import LocalStorageBackend


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


@pytest.fixture
def outside_file(tmp_path: Path) -> Path:
    """A file living outside the storage root, as an operator's config might."""
    p = tmp_path / "SECRET.txt"
    p.write_text("outside the storage root")
    return p


class TestSessionIdTraversal:
    def test_percent_encoded_session_id_is_404_not_200(self, tmp_path: Path) -> None:
        """The exact request run 3 used.

        A literal ".." is collapsed by well-behaved HTTP clients before it is
        sent, which is most likely why this looked closed; "%2e%2e" survives the
        client and decodes to ".." server-side.
        """
        storage_path = tmp_path / "sessions"
        storage_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "report.html").write_text("<b>TOP SECRET - outside session dir</b>")

        app = create_app(
            settings=Settings(
                storage_path=str(storage_path),
                anthropic_api_key="test-key",
                osrm_host="http://localhost:5000",
                admin_token="test-admin-token",
                storage_backend="local",
            )
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/sessions/%2e%2e/report.html")

        assert resp.status_code == 404, (
            f"path traversal via percent-encoded session_id served a file "
            f"outside the storage root: status={resp.status_code} body={resp.text!r}"
        )

    def test_traversing_session_id_cannot_read(
        self, storage: LocalStorageBackend, outside_file: Path
    ) -> None:
        with pytest.raises(ValueError, match="escapes the storage root"):
            asyncio.run(storage.read("..", "SECRET.txt"))

    def test_absolute_session_id_cannot_read(
        self, storage: LocalStorageBackend, outside_file: Path
    ) -> None:
        """pathlib's join lets an absolute part discard the base entirely."""
        with pytest.raises(ValueError, match="escapes the storage root"):
            asyncio.run(storage.read(str(outside_file.parent), "SECRET.txt"))

    def test_traversing_session_id_cannot_write(self, storage: LocalStorageBackend) -> None:
        with pytest.raises(ValueError, match="escapes the storage root"):
            asyncio.run(storage.write("..", "planted.txt", b"x"))


class TestFilenameTraversal:
    """`filename` is caller-supplied too. Confining only the directory would
    leave this open; it currently fails only because the session directory
    usually does not exist yet, which is luck rather than a guarantee."""

    def test_traversing_filename_cannot_read(
        self, storage: LocalStorageBackend, outside_file: Path
    ) -> None:
        asyncio.run(storage.write("real-session", "ok.txt", b"x"))  # make the dir exist
        with pytest.raises(ValueError, match="escapes the storage root"):
            asyncio.run(storage.read("real-session", "../../SECRET.txt"))


class TestExistsIsFalseNeverRaises:
    """Every artifact route asks exists() first and 404s on False.

    Returning False turns a traversal into the 404 it deserves; letting the
    ValueError out would make it a 500 — an uncaught exception escaping the
    handler, the same shape as run 2's negative-service_time crash, and a hint
    to the caller that their path was interesting.
    """

    def test_escaping_path_does_not_exist(
        self, storage: LocalStorageBackend, outside_file: Path
    ) -> None:
        assert asyncio.run(storage.exists("..", "SECRET.txt")) is False

    def test_real_path_still_exists(self, storage: LocalStorageBackend) -> None:
        asyncio.run(storage.write("abc123", "report.html", b"<b>ok</b>"))
        assert asyncio.run(storage.exists("abc123", "report.html")) is True


class TestDeleteSessionCannotEscape:
    """The sharpest end of the same hole: delete_session rmtree's what it gets,
    so a traversing id deleted an arbitrary directory outside the root."""

    def test_traversing_id_cannot_rmtree_outside_root(
        self, storage: LocalStorageBackend, tmp_path: Path
    ) -> None:
        victim = tmp_path / "VICTIM"
        victim.mkdir()
        (victim / "data.txt").write_text("important")

        with pytest.raises(ValueError, match="escapes the storage root"):
            asyncio.run(storage.delete_session("../VICTIM"))
        assert victim.exists(), "delete_session rmtree'd a directory outside the storage root"

    def test_empty_id_cannot_rmtree_the_root_itself(
        self, storage: LocalStorageBackend, tmp_path: Path
    ) -> None:
        """An empty or "." id resolves to the root, which is *inside* the root
        and so passes confinement — it would rmtree every session there is."""
        asyncio.run(storage.write("keep-me", "f.txt", b"x"))
        for bad_id in ("", "."):
            with pytest.raises(ValueError, match="Refusing to delete the storage root"):
                asyncio.run(storage.delete_session(bad_id))
        assert asyncio.run(storage.exists("keep-me", "f.txt")) is True

    def test_real_session_still_deletable(self, storage: LocalStorageBackend) -> None:
        asyncio.run(storage.write("doomed", "f.txt", b"x"))
        asyncio.run(storage.delete_session("doomed"))
        assert asyncio.run(storage.exists("doomed", "f.txt")) is False


class TestRegistryLookupCannotEscape:
    """`SessionRegistry.get` reads status.json, so it is a traversal door too.

    It caught only FileNotFoundError, while a traversing id makes `read` raise
    ValueError. That was harmless for exactly as long as every artifact route
    happened to call `exists()` (which returns False) before `get()`. Adding an
    expiry check that calls `get()` first turned `GET /sessions/%2e%2e/report.html`
    back into a 500 — the same URL from robustness run 3, reopened by a change
    nowhere near the original fix.

    That is the third instance of this root cause in this codebase, so it gets
    a test at the registry rather than only at the route.
    """

    def test_traversing_id_returns_none_not_valueerror(self, storage: LocalStorageBackend) -> None:
        registry = SessionRegistry(storage)
        for bad_id in ("../..", "%2e%2e", "/etc", "../../SECRET"):
            assert asyncio.run(registry.get(bad_id)) is None

    def test_artifact_route_with_traversing_id_is_404_not_500(self, tmp_path: Path) -> None:
        settings = Settings(
            storage_path=str(tmp_path / "sessions"),
            anthropic_api_key="test-key",
            admin_token="test-admin-token",
            storage_backend="local",
        )
        client = TestClient(create_app(settings=settings), raise_server_exceptions=False)
        for artifact in ("report.html", "report.pdf", "analysis.json", "routes.geojson"):
            resp = client.get(f"/sessions/%2e%2e/{artifact}")
            assert resp.status_code == 404, f"{artifact} leaked a {resp.status_code}"


class TestDeleteFileCannotEscape:
    """`delete_file` is the newest door onto the same hole, and it unlinks.

    Softer than `delete_session` — it removes one file rather than a tree — but
    it is called in a loop by the retention job over every session in storage,
    so a raise here would abort the sweep for everything after it. Hence False
    rather than an exception, matching `exists`.
    """

    def test_traversing_filename_cannot_unlink_outside_root(
        self, storage: LocalStorageBackend, outside_file: Path
    ) -> None:
        assert asyncio.run(storage.delete_file("s1", "../../SECRET.txt")) is False
        assert outside_file.exists(), "delete_file unlinked a file outside the storage root"

    def test_traversing_session_id_cannot_unlink_outside_root(
        self, storage: LocalStorageBackend, outside_file: Path
    ) -> None:
        assert asyncio.run(storage.delete_file("../..", "SECRET.txt")) is False
        assert outside_file.exists()

    def test_missing_file_is_false_not_an_error(self, storage: LocalStorageBackend) -> None:
        """Retention re-sweeps expired sessions hourly; the second pass finds
        nothing and must not raise."""
        assert asyncio.run(storage.delete_file("nope", "nothing.txt")) is False

    def test_real_file_is_deleted_and_reported(self, storage: LocalStorageBackend) -> None:
        asyncio.run(storage.write("s1", "upload.csv", b"x"))
        assert asyncio.run(storage.delete_file("s1", "upload.csv")) is True
        assert asyncio.run(storage.exists("s1", "upload.csv")) is False
