"""Settings resolution — the parts that broke when the app was launched from
a directory other than the repo root.

Both regressions here had the same shape: a cwd-relative default that silently
did the wrong thing rather than failing. `env_file=".env"` loaded nothing (and
so disabled CORS) when uvicorn started from a parent directory; `storage_path`
scattered session artifacts into whatever directory the process happened to
start in, and hid sessions written by a run started elsewhere. A launcher that
targets the project by path — which is exactly how the dev preview runs it —
triggered both.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from routebench.core.config import _REPO_ROOT, Settings


def test_repo_root_points_at_the_repo() -> None:
    """The anchor is the repo, identified by a file only the repo root has."""
    assert (_REPO_ROOT / "pyproject.toml").is_file()


def test_storage_and_cache_defaults_are_absolute_and_under_the_repo() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    for path in (settings.storage_path, settings.matrix_cache_path):
        assert os.path.isabs(path), f"{path} is cwd-relative and will scatter by launch dir"
        assert Path(path).is_relative_to(_REPO_ROOT)


def test_defaults_do_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: same answer from any cwd. Compare a run from the repo
    root against a run from an unrelated temp directory."""
    monkeypatch.chdir(_REPO_ROOT)
    from_root = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.chdir(tmp_path)
    from_elsewhere = Settings(_env_file=None)  # type: ignore[call-arg]
    assert from_root.storage_path == from_elsewhere.storage_path
    assert from_root.matrix_cache_path == from_elsewhere.matrix_cache_path


def test_unknown_env_keys_are_ignored_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shared .env carries compose-only keys (OSRM_REGION); forbidding extras
    turned that into a startup crash."""
    monkeypatch.setenv("OSRM_REGION", "texas-latest")
    monkeypatch.setenv("SOME_FUTURE_COMPOSE_KEY", "x")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.osrm_host  # constructed successfully, unknown keys ignored


def test_explicit_override_still_wins() -> None:
    """Anchoring changes the default only; an operator's absolute path is kept."""
    settings = Settings(storage_path="/data/sessions", _env_file=None)  # type: ignore[call-arg]
    assert settings.storage_path == "/data/sessions"
