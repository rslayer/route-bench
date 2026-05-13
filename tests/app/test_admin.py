"""Tests for admin API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from routebench.app.api.app import create_app
from routebench.core.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_path=str(tmp_path / "sessions"),
        anthropic_api_key="test-key",
        admin_token="test-token",
        storage_backend="local",
    )


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    app = create_app(settings=settings)
    return TestClient(app, raise_server_exceptions=False)


class TestAdminAuth:
    """Tests for admin authentication."""

    def test_missing_token(self, client: TestClient) -> None:
        resp = client.get("/admin/sessions")
        assert resp.status_code in (403, 422)

    def test_wrong_token(self, client: TestClient) -> None:
        resp = client.get(
            "/admin/sessions",
            headers={"x-admin-token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_valid_token(self, client: TestClient) -> None:
        resp = client.get(
            "/admin/sessions",
            headers={"x-admin-token": "test-token"},
        )
        assert resp.status_code == 200


class TestAdminSessions:
    """Tests for admin session listing."""

    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get(
            "/admin/sessions",
            headers={"x-admin-token": "test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"] == []
        assert body["total"] == 0


class TestAdminCosts:
    """Tests for admin cost endpoints."""

    def test_costs_empty(self, client: TestClient) -> None:
        resp = client.get(
            "/admin/costs",
            headers={"x-admin-token": "test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
