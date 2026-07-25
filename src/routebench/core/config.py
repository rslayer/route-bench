"""Configuration for RouteBench.

Uses pydantic-settings with environment variable overrides.
"""

from __future__ import annotations

import hashlib
import json
from datetime import time
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The repository root, so the .env file is found regardless of the working
# directory the process was launched from. This module is
# src/routebench/core/config.py, so the root is three parents up. env_file=".env"
# resolves against the cwd, which silently loaded nothing — and therefore
# silently disabled CORS — whenever the app was started from anywhere but the
# repo root (a launcher targeting the project by path, a container WORKDIR, a
# monorepo parent). An absolute anchor removes that failure mode.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Claude pricing (per 1M tokens)
CLAUDE_INPUT_PRICE_PER_M: float = 3.0
CLAUDE_OUTPUT_PRICE_PER_M: float = 15.0

# Upload limits
MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024  # 50 MB


class WorkRules(BaseModel):
    """Work rules governing shift constraints.

    Bounds are load-bearing, not decoration. This model is the boundary between
    a user-supplied config JSON and the analysis, and the models downstream have
    their own constraints (Stop.service_time_minutes is ge=0). An unbounded
    value here is accepted, carried inward, and then rejected by a model that
    was never meant to face user input — surfacing as a 500 rather than the 422
    the caller deserves. Reject it at the door.
    """

    # extra="forbid": pydantic's default is to ignore unknown keys, so
    # `{"work_rules": {"mx_shift_hours": 8}}` was accepted, silently fell back to
    # the default, and the caller got a clean 202 for an analysis that ignored the
    # constraint they set. A misspelled key is a mistake worth a 422.
    model_config = ConfigDict(extra="forbid")

    max_shift_hours: float = Field(default=12.0, gt=0, le=24)
    pre_trip_minutes: float = Field(default=15.0, ge=0, le=1440)
    post_trip_minutes: float = Field(default=15.0, ge=0, le=1440)
    lunch_minutes: float = Field(default=30.0, ge=0, le=1440)
    lunch_after_hours: float = Field(default=6.0, ge=0, le=24)
    enforce_time_windows: bool = True
    # Capacity was previously decided purely by whether the upload carried
    # capacity columns, so there was no way to ask "how would this plan look
    # without the capacity constraint?" — and the constraints panel had nothing
    # to bind a checkbox to. Still a no-op when the data has no capacity.
    enforce_capacity: bool = True


class ServiceTimeModel(BaseModel):
    """Service time defaults and overrides."""

    model_config = ConfigDict(extra="forbid")

    # ge=0 mirrors Stop.service_time_minutes. Without it a negative default was
    # accepted here and then rejected by Stop deep inside validate_csv, turning
    # a bad request into a 500.
    default_minutes: float = Field(default=5.0, ge=0, le=1440)


class TrafficBand(BaseModel):
    """A time-of-day band that scales free-flow travel speed.

    `start` and `end` are depot-local wall-clock times, matching how the rest of
    the pipeline reads `planned_start_time` (see analysis.scoring.time).
    """

    model_config = ConfigDict(extra="forbid")

    start: time  # inclusive
    end: time  # exclusive
    # Upper-bounded and inf/nan-rejecting, not just `gt=0`: `inf > 0` is True, so a
    # bare gt=0 accepted `1e400` -> inf, and every adjusted travel time collapsed to
    # 0. 10x free-flow is already far past anything a real profile describes.
    speed_factor: float = Field(gt=0, le=10.0, allow_inf_nan=False)

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

    model_config = ConfigDict(extra="forbid")

    bands: list[TrafficBand] = Field(default_factory=list)
    default_factor: float = Field(default=1.0, gt=0, le=10.0, allow_inf_nan=False)

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

    model_config = ConfigDict(extra="forbid")

    work_rules: WorkRules = Field(default_factory=WorkRules)
    service_time: ServiceTimeModel = Field(default_factory=ServiceTimeModel)
    traffic: TrafficProfile = Field(default_factory=TrafficProfile)
    sequencing_threshold: float = Field(default=1.30, gt=0)
    underutilization_threshold: float = Field(default=0.60, ge=0, le=1)
    overutilization_threshold: float = Field(default=0.95, ge=0, le=1)
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
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore, not forbid: a single .env is shared with docker-compose, which
        # reads keys the app never sees (OSRM_REGION locates the graph for the
        # OSRM container). Forbidding extras crashed the app on startup the
        # moment such a key appeared in .env. The nested models above keep
        # extra="forbid" — those parse trusted internal payloads, not a file an
        # operator hand-edits and shares with other tools.
        extra="ignore",
    )

    # LLM
    #
    # This must be a model ID the API actually serves. The previous default,
    # "claude-sonnet-4-7", was not one — no such model exists — so every call
    # would have 404'd the moment a real key was configured, in a way that
    # reads exactly like a bad key. Check the ID against the current model list
    # before changing it; do not pattern-match a new one from an old string.
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-8"

    # Which engine computes the origin-destination matrix.
    #
    # "osrm" (default) — self-hosted, free-flow times, no per-request cost.
    # "google" — Google Routes computeRouteMatrix with live traffic. Needs
    #   google_maps_api_key and bills per element (origins x destinations), so
    #   it is off unless deliberately selected. When active it returns real
    #   traffic-adjusted times directly, and the per-upload traffic profile
    #   (the free-flow band multiplier) is not applied on top — that would
    #   double-count congestion.
    #
    # Whatever the engine, the haversine fallback still catches an outage, and
    # the grade is withheld on an approximate result. Selecting "google" with
    # no key is a configuration error caught at startup rather than a silent
    # fall-through to OSRM.
    matrix_engine: Literal["osrm", "google"] = "osrm"

    # OSRM
    osrm_host: str = "http://localhost:5000"

    # Google Routes API key, used only when matrix_engine == "google". Set as a
    # deployment secret, never entered in the web UI — a routing key is a
    # billable credential and must not transit the frontend or land in a
    # per-upload config.
    google_maps_api_key: str = ""

    # Filesystem cache for OSRM matrices. Wraps OSRM only, never the fallback,
    # so a cache hit is always a real road-network answer. Approximate results
    # are refused by the cache itself as a second line of defence.
    #
    # On by default: matrix calls dominate wall-clock on repeat uploads of the
    # same territory, and the cache key is content-addressed, so a stale entry
    # is only possible if the road network itself changed. Point this at a
    # persistent volume in production or it warms from cold on every deploy.
    matrix_cache_enabled: bool = True
    # Repo-root-anchored like storage_path, so the cache is one directory rather
    # than one per launch cwd. A deployment overrides with an absolute path on a
    # persistent volume.
    matrix_cache_path: str = str(_REPO_ROOT / "data" / "matrix-cache")
    # Per-leg cache knobs. Coordinates are snapped to this many decimal places so
    # the same stop recurring across fleets (a depot, a repeat customer) reuses
    # its legs even when the float is not bit-identical: 4 dp is ~11 m — distinct
    # delivery stops are rarely closer, so collisions are rare, while an exact
    # address matches itself. Lower it for more aggressive reuse at the cost of
    # snapping nearby-but-different stops together.
    matrix_cache_snap_decimals: int = Field(default=4, ge=0, le=6)
    # A cached leg is trusted for this long. Typical time-of-day travel is stable
    # week to week, but Google's model drifts and roads change, so legs expire
    # and are refetched rather than served forever.
    matrix_cache_ttl_days: float = Field(default=7.0, gt=0)

    # Basemap tiles for the report's route images. See infra/tiles.py.
    #
    # Off by default deliberately: the default would otherwise be an outbound
    # call to a donated public service on every report, which is not a sensible
    # thing to opt a deployment into silently. Routes still render, on a plain
    # background. Turn it on and the OSM defaults apply; point map_tile_url at
    # your own host if you serve real traffic.
    map_tiles_enabled: bool = False
    map_tile_url: str = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_tile_attribution: str = "© OpenStreetMap contributors"
    map_tile_timeout_seconds: float = 5.0

    # Web UI origin(s) allowed to call this API cross-origin. Comma-separated;
    # empty disables CORS entirely (the co-hosted Streamlit case, same-origin).
    # Never set this to "*": the API hands out session artifacts addressable by
    # an unguessable URL, and a wildcard would let any page that learns a
    # session id read its report.
    web_origin: str = ""

    # General
    log_level: str = "INFO"
    # Anchored to the repo root, not "./data/sessions", for the same reason as
    # env_file: a cwd-relative default scatters session artifacts into whatever
    # directory the process happened to start in — and, worse, hides sessions
    # written by a run started from a different directory. A deployment sets an
    # absolute STORAGE_PATH (docker uses /data/sessions) which overrides this.
    storage_path: str = str(_REPO_ROOT / "data" / "sessions")

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

    # Daily cap on matrix-engine spend, in USD. Only meaningful on a metered
    # engine (Google, which bills per element); OSRM and haversine are free, so
    # this does nothing there. 0 disables the cap — the default, because most
    # deployments do not need one and a cap that silently degrades every run
    # would surprise. Once today's estimated matrix spend reaches this, further
    # runs fall back to haversine estimates (grade withheld) instead of billing
    # more, and it resets at UTC midnight. Set it well above a normal day's spend
    # so it is a runaway-bill backstop, not a routine limiter. Put a hard quota
    # on the key in the Google console too — this is a courtesy, not enforcement.
    daily_matrix_budget_usd: float = 0.0

    # Admin
    admin_token: str = ""

    # Sentry
    sentry_dsn: str = ""

    def web_origins(self) -> list[str]:
        """Parsed CORS allowlist. Empty means same-origin only."""
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]
