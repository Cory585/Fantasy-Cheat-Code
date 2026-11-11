"""Application settings. Extend with env-driven config (API keys, DB URLs) as needed."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CACHE_PATH = DATA_DIR / "players.json"
DEFAULT_DATABASE_PATH = DATA_DIR / "fantasy.db"


@dataclass(frozen=True)
class Settings:
    app_name: str = "Fantasy Cheat Code API"
    version: str = "0.1.0"
    # Default league scoring format used when a request doesn't specify one.
    default_scoring: str = "ppr"
    # A trade is considered "fair" if value difference is within this percentage.
    fair_trade_threshold_pct: float = 10.0
    # Player cache written by `python -m app.data.sync`. Sample data is used if it doesn't exist.
    player_cache_path: Path = field(
        default_factory=lambda: Path(os.getenv("PLAYER_CACHE_PATH", str(DEFAULT_CACHE_PATH)))
    )
    # SQLite database of collected player/team data, written by `python -m app.data.collect`.
    database_path: Path = field(
        default_factory=lambda: Path(os.getenv("DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))
    )
    # Share of a player's projection that comes from schedule-adjusted recent form
    # (the rest comes from the Sleeper/ESPN projection). 0 turns form off.
    form_weight: float = field(default_factory=lambda: float(os.getenv("FORM_WEIGHT", "0.3")))
    # Minimum seconds between requests to the same external host (rate limiting).
    request_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("REQUEST_INTERVAL_SECONDS", "1.0"))
    )
    # Cache of ESPN player profiles, written while the API runs (kept apart from the database).
    profile_cache_path: Path = field(
        default_factory=lambda: Path(os.getenv("PROFILE_CACHE_PATH", str(DATA_DIR / "profiles.db")))
    )
    # How long a fetched ESPN profile is reused before it's fetched again.
    profile_ttl_hours: float = field(default_factory=lambda: float(os.getenv("PROFILE_TTL_HOURS", "24")))
    # Incoming rate limits per client IP (see app/api/ratelimit.py). RATE_LIMIT_ENABLED=0 turns them off.
    rate_limit_enabled: bool = field(default_factory=lambda: os.getenv("RATE_LIMIT_ENABLED", "1") != "0")
    # Key clients on X-Forwarded-For. Only enable behind a proxy you control that sets it.
    trust_proxy: bool = field(default_factory=lambda: os.getenv("TRUST_PROXY", "0") == "1")
    # Number of proxies in front of the app that each append to X-Forwarded-For.
    trusted_proxy_hops: int = field(default_factory=lambda: int(os.getenv("TRUSTED_PROXY_HOPS", "1")))


settings = Settings()
