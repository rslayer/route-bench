"""Configuration for RouteBench.

Uses pydantic-settings with environment variable overrides.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    include_pdf: bool = False


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-7"
    osrm_host: str = "http://localhost:5000"
    log_level: str = "INFO"
    storage_path: str = "./data/sessions"
