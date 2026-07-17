"""Defect: unauthenticated path traversal via `session_id` in the download routes.

Impact: `GET /sessions/{session_id}/report.html` (and the sibling report.pdf,
analysis.json, routes.geojson routes) pass the raw, attacker-controlled
`session_id` path segment straight into
``LocalStorageBackend.exists(session_id, filename)`` /
``.read(session_id, filename)``. Those methods build the path as
``self._base / session_id / filename`` with no confinement check — unlike
``LocalStorageBackend._object_path``, which explicitly resolves and verifies
the result stays under its root (see the docstring in
``routebench/infra/storage/local.py``). No such check exists for the
session-based read/write/exists/delete_session path, so `session_id` can walk
out of the storage root with an ordinary ".." path segment.

A literal ".." in the URL gets normalized away by well-behaved HTTP clients
(httpx/browsers collapse "/sessions/../report.html" to "/report.html" before
it is ever sent), so that alone does not reach the server. But a
percent-encoded segment defeats client-side normalization while still
decoding to ".." on the server: `GET /sessions/%2e%2e/report.html` is sent
byte-for-byte, decoded server-side to session_id="..", and
`_session_dir("..")` resolves one directory above the configured storage
root. Any file placed there (or reachable by chaining directory names an
attacker can predict/create) is served back with a 200, unauthenticated,
before any admin token or session-ownership check ever runs.

Root cause: `LocalStorageBackend._session_dir` (routebench/infra/storage/local.py)
does not apply the same "resolve and verify it's still under the root" check
that `_object_path` does two methods below it in the same file.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from routebench.app.api.app import create_app
from routebench.core.config import Settings


def test_percent_encoded_session_id_escapes_storage_root(tmp_path: Path) -> None:
    storage_path = tmp_path / "sessions"
    settings = Settings(
        storage_path=str(storage_path),
        anthropic_api_key="test-key",
        osrm_host="http://localhost:5000",
        admin_token="test-admin-token",
        storage_backend="local",
    )
    app = create_app(settings=settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Storage root must exist for the app to be usable at all.
    storage_path.mkdir(parents=True, exist_ok=True)

    # A file that lives OUTSIDE the session storage root (one level up), the
    # way a co-located but unrelated file might, e.g. an operator's config
    # dropped next to the data directory.
    secret = tmp_path / "report.html"
    secret.write_text("<b>TOP SECRET - outside session dir</b>")

    resp = client.get("/sessions/%2e%2e/report.html")

    # Expected (correct) behaviour: session_id ".." does not name a real
    # session, so this should 404 like any other unknown session.
    # Actual (defect): the traversal escapes the storage root and the
    # attacker-planted file outside it is served with a 200.
    assert resp.status_code == 404, (
        f"path traversal via percent-encoded session_id served a file outside "
        f"the storage root: status={resp.status_code} body={resp.text!r}"
    )
