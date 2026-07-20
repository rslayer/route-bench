"""The matrix-engine selector — `_build_primary_matrix_provider`.

Pins the config → provider mapping and, most importantly, that selecting
"google" without a key fails at startup rather than silently falling through to
OSRM or 403-ing partway through the first analysis.
"""

from __future__ import annotations

import pytest

from routebench.app.api.app import _build_primary_matrix_provider
from routebench.core.config import Settings
from routebench.infra.matrix.google import GoogleMatrixProvider
from routebench.infra.matrix.osrm import OSRMMatrixProvider


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "anthropic_api_key": "test",
        "admin_token": "test",
        "storage_backend": "local",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_default_engine_is_osrm() -> None:
    provider = _build_primary_matrix_provider(_settings())
    assert isinstance(provider, OSRMMatrixProvider)


def test_google_engine_with_key() -> None:
    provider = _build_primary_matrix_provider(
        _settings(matrix_engine="google", google_maps_api_key="a-key")
    )
    assert isinstance(provider, GoogleMatrixProvider)


def test_google_engine_without_key_fails_loudly() -> None:
    """The whole point of failing here is to not accept uploads against a
    misconfigured engine and only discover it mid-run."""
    with pytest.raises(ValueError, match="GOOGLE_MAPS_API_KEY"):
        _build_primary_matrix_provider(_settings(matrix_engine="google"))
