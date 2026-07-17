"""Configuration for RouteBench.

Uses pydantic-settings with environment variable overrides.
"""

from __future__ import annotations

import hashlib
import json
from datetime import time
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
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
    # Capacity was previously decided purely by whether the upload carried
    # capacity columns, so there was no way to ask "how would this plan look
    # without the capacity constraint?" — and the constraints panel had nothing
    # to bind a checkbox to. Still a no-op when the data has no capacity.
    enforce_capacity: bool = True


class ServiceTimeModel(BaseModel):
    """Service time defaults and overrides."""

    default_minutes: float = 5.0


class TrafficBand(BaseModel):
    """A time-of-day band that scales free-flow travel speed.

    `start` and `end` are depot-local wall-clock times, matching how the rest of
    the pipeline reads `planned_start_time` (see analysis.scoring.time).
    """

    start: time  # inclusive
    end: time  # exclusive
    speed_factor: float = Field(gt=0)  # multiplies free-flow speed; <1.0 slows travel

    @model_validator(mode="after")
    def _start_must_precede_end(self) -> TrafficBand:
        """Bands may not wrap past midnight; split them instead."""
        if self.start >= self.end:
            msg = (
                f"TrafficBand start must precede end (bands cannot wrap past "
                f"midnight; use two bands): got {self.start}-{self.end}"
            )
            raise ValueError(msg)
        return self


class TrafficProfile(BaseModel):
    """Time-banded speed multipliers applied on top of free-flow travel times.

    An empty `bands` list with `default_factor` 1.0 is the identity profile and
    reproduces free-flow behavior exactly.
    """

    bands: list[TrafficBand] = Field(default_factory=list)
    default_factor: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _bands_must_not_overlap(self) -> TrafficProfile:
        """Overlapping bands would make factor lookup order-dependent."""
        ordered = sorted(self.bands, key=lambda b: b.start)
        for earlier, later in pairwise(ordered):
            if later.start < earlier.end:
                msg = (
                    f"TrafficProfile bands must not overlap: "
                    f"{earlier.start}-{earlier.end} overlaps {later.start}-{later.end}"
                )
                raise ValueError(msg)
        return self

    @property
    def is_active(self) -> bool:
        """True when this profile would change any travel time."""
        return bool(self.bands) or self.default_factor != 1.0

    def factor_at(self, t: time) -> float:
        """Return the speed factor for a local wall-clock time."""
        for band in self.bands:
            if band.start <= t < band.end:
                return band.speed_factor
        return self.default_factor

    def profile_hash(self) -> str:
        """Stable hash used to keep cache entries from different profiles apart."""
        payload = {
            "bands": sorted(
                [b.start.isoformat(), b.end.isoformat(), round(b.speed_factor, 6)]
                for b in self.bands
            ),
            "default_factor": round(self.default_factor, 6),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


URBAN_US_PROFILE = TrafficProfile(
    bands=[
        TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.75),
        TrafficBand(start=time(16, 0), end=time(18, 30), speed_factor=0.80),
    ],
    default_factor=1.0,
)

NAMED_TRAFFIC_PROFILES: dict[str, TrafficProfile] = {"urban_us": URBAN_US_PROFILE}


class AnalysisConfig(BaseModel):
    """Configuration for the analysis pipeline."""

    work_rules: WorkRules = Field(default_factory=WorkRules)
    service_time: ServiceTimeModel = Field(default_factory=ServiceTimeModel)
    traffic: TrafficProfile = Field(default_factory=TrafficProfile)
    sequencing_threshold: float = 1.30
    underutilization_threshold: float = 0.60
    overutilization_threshold: float = 0.95
    include_benchmark: bool = True
    include_pdf: bool = False

    # OR-Tools' guided local search runs until its time limit rather than
    # stopping when it converges, so these are spent in full whenever the
    # matching benchmark runs. The per-route limit is paid once per route, so
    # total solve time is roughly:
    #   n_routes * route_benchmark_time_limit_s + fleet_benchmark_time_limit_s
    # Keep that under Settings.job_timeout_seconds for the fleet sizes you accept.
    route_benchmark_time_limit_s: int = Field(default=30, gt=0)
    fleet_benchmark_time_limit_s: int = Field(default=120, gt=0)

    @field_validator("traffic", mode="before")
    @classmethod
    def _resolve_named_profile(cls, v: object) -> object:
        """Accept a named profile (e.g. "urban_us") in place of an inline profile."""
        if isinstance(v, str):
            profile = NAMED_TRAFFIC_PROFILES.get(v)
            if profile is None:
                msg = (
                    f"Unknown traffic profile {v!r}; "
                    f"known profiles: {sorted(NAMED_TRAFFIC_PROFILES)}"
                )
                raise ValueError(msg)
            return profile.model_dump()
        return v


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

    # Web UI origin(s) allowed to call this API cross-origin. Comma-separated;
    # empty disables CORS entirely (the co-hosted Streamlit case, same-origin).
    # Never set this to "*": the API hands out session artifacts addressable by
    # an unguessable URL, and a wildcard would let any page that learns a
    # session id read its report.
    web_origin: str = ""

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

    def web_origins(self) -> list[str]:
        """Parsed CORS allowlist. Empty means same-origin only."""
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]
