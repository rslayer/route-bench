"""Configuration for RouteBench.

Uses pydantic-settings with environment variable overrides.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Claude pricing (per 1M tokens)
CLAUDE_INPUT_PRICE_PER_M: float = 3.0
CLAUDE_OUTPUT_PRICE_PER_M: float = 15.0

# Upload limits
MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024  # 50 MB


class WorkRules(BaseModel):
    """Work rules governing shift constraints."""

    max_shift_hours: float = 12.0
    pre_trip_minutes: float = 15.0
    post_trip_minutes: float = 15.0
    lunch_minutes: float = 30.0
    lunch_after_hours: float = 6.0
    enforce_time_windows: bool = True


class ServiceTimeModel(BaseModel):
    """Service time defaults and overrides."""

    default_minutes: float = 5.0


class AnalysisConfig(BaseModel):
    """Configuration for the analysis pipeline."""

    work_rules: WorkRules = Field(default_factory=WorkRules)
    service_time: ServiceTimeModel = Field(default_factory=ServiceTimeModel)
    sequencing_threshold: float = 1.30
    underutilization_threshold: float = 0.60
    overutilization_threshold: float = 0.95
    include_benchmark: bool = True
    include_pdf: bool = False


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-7"

    # OSRM
    osrm_host: str = "http://localhost:5000"

    # General
    log_level: str = "INFO"
    storage_path: str = "./data/sessions"

    # Storage backend
    storage_backend: Literal["local", "s3"] = "local"

    # R2 / S3-compatible storage
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "routebench"
    r2_region: str = "auto"

    # Worker / queue
    max_queue_depth: int = 5
    job_timeout_seconds: int = 600

    # Session retention
    session_ttl_hours: int = 72
    telemetry_ttl_hours: int = 720  # 30 days

    # Cost guardrails
    daily_budget_usd: float = 50.0
    max_input_tokens_per_session: int = 500_000

    # Admin
    admin_token: str = ""

    # Sentry
    sentry_dsn: str = ""
