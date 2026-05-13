"""Session state management — in-memory registry + storage persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from routebench.infra.storage.base import StorageBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

SessionState = Literal[
    "queued",
    "validating",
    "analyzing",
    "writing",
    "rendering",
    "succeeded",
    "failed",
]


class SessionError(BaseModel):
    """Error detail for a failed session."""

    code: str
    message: str
    context: dict[str, object] = Field(default_factory=dict)


class SessionArtifacts(BaseModel):
    """Artifact locations for a completed session."""

    report_html: str = ""
    report_pdf: str = ""
    analysis_json: str = ""
    telemetry_json: str = ""


class CostSummary(BaseModel):
    """Cost breakdown for a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    llm_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


class SessionStatus(BaseModel):
    """Full session status — single source of truth."""

    session_id: str
    state: SessionState = "queued"
    progress_pct: int = 0
    stage_detail: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: SessionError | None = None
    artifacts: SessionArtifacts | None = None
    cost: CostSummary | None = None


class SessionRegistry:
    """In-process registry for active sessions, backed by storage for completed ones."""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage
        self._active: dict[str, SessionStatus] = {}

    def create(self, session_id: str) -> SessionStatus:
        now = datetime.now(UTC)
        status = SessionStatus(
            session_id=session_id,
            state="queued",
            progress_pct=0,
            stage_detail="Queued for processing",
            created_at=now,
            updated_at=now,
        )
        self._active[session_id] = status
        return status

    def update(
        self,
        session_id: str,
        *,
        state: SessionState | None = None,
        progress_pct: int | None = None,
        stage_detail: str | None = None,
        error: SessionError | None = None,
        artifacts: SessionArtifacts | None = None,
        cost: CostSummary | None = None,
    ) -> SessionStatus | None:
        status = self._active.get(session_id)
        if status is None:
            return None
        if state is not None:
            status.state = state
        if progress_pct is not None:
            status.progress_pct = progress_pct
        if stage_detail is not None:
            status.stage_detail = stage_detail
        if error is not None:
            status.error = error
        if artifacts is not None:
            status.artifacts = artifacts
        if cost is not None:
            status.cost = cost
        status.updated_at = datetime.now(UTC)
        return status

    async def get(self, session_id: str) -> SessionStatus | None:
        if session_id in self._active:
            return self._active[session_id]
        # Try loading from storage
        try:
            data = await self._storage.read(session_id, "status.json")
            return SessionStatus.model_validate_json(data)
        except FileNotFoundError:
            return None

    async def persist(self, session_id: str) -> None:
        status = self._active.get(session_id)
        if status is None:
            return
        data = status.model_dump_json(indent=2).encode()
        await self._storage.write(session_id, "status.json", data)

    def remove_active(self, session_id: str) -> None:
        self._active.pop(session_id, None)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_sessions(self) -> dict[str, SessionStatus]:
        return dict(self._active)

    async def list_all(self, since: datetime | None = None) -> list[SessionStatus]:
        """List all sessions from storage, optionally filtering by date."""
        results: list[SessionStatus] = []
        session_ids = await self._storage.list_sessions()
        for sid in session_ids:
            try:
                data = await self._storage.read(sid, "status.json")
                status = SessionStatus.model_validate_json(data)
                if since is None or status.created_at >= since:
                    results.append(status)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        return sorted(results, key=lambda s: s.created_at, reverse=True)
