"""Build identity — what the footer renders as `v{X.Y.Z} · build {short_sha}`.

The version comes from the installed package metadata, which hatchling reads
from pyproject.toml, so it cannot drift from the declared version the way a
hardcoded string does.

The commit is read from the GIT_SHA environment variable, baked in at image
build time (`--build-arg`/`ENV` in the Dockerfile, or `fly deploy` supplying
it). A running container has no .git directory, so there is nowhere else to read
it from; when it is absent — local dev, a plain `uv run` — the sha is reported
as "unknown" rather than guessed.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

_UNKNOWN_SHA = "unknown"


def package_version() -> str:
    """The distribution version, from package metadata (pyproject is the source)."""
    try:
        return _pkg_version("routebench")
    except PackageNotFoundError:
        # Running from a source tree that was never installed.
        return "0.0.0+unknown"


def git_sha() -> str:
    """Short commit sha, or "unknown" when the build did not bake one in."""
    sha = os.environ.get("GIT_SHA", "").strip()
    return sha[:7] if sha else _UNKNOWN_SHA


def build_info() -> dict[str, str]:
    """Version identity for /health and the web footer."""
    return {
        "version": package_version(),
        "commit": git_sha(),
    }
