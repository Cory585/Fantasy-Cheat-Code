"""Application settings. Extend with env-driven config (API keys, DB URLs) as needed."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CACHE_PATH = DATA_DIR / "players.json"
DEFAULT_DATABASE_PATH = DATA_DIR / "fantasy.db"
# Last week of the NFL regular season (17 games over 18 weeks since 2021).
REGULAR_SEASON_END_WEEK = 18


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
    # Kicker and DST models written by `python -m app.kicker.train` / `python -m app.defense.train`.
    # Start/sit uses them when present.
    dst_model_path: Path = field(
        default_factory=lambda: Path(os.getenv("DST_MODEL_PATH", str(DATA_DIR / "defense_model.json")))
    )
    kicker_model_path: Path = field(
        default_factory=lambda: Path(os.getenv("KICKER_MODEL_PATH", str(DATA_DIR / "kicker_model.json")))
    )
    # Share of a kicker's or defense's weekly projection that comes from its matchup model.
    model_weight: float = field(default_factory=lambda: float(os.getenv("MODEL_WEIGHT", "0.5")))
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
    # The leagues on each profile, written while the API runs.
    league_db_path: Path = field(
        default_factory=lambda: Path(os.getenv("LEAGUE_DB_PATH", str(DATA_DIR / "leagues.db")))
    )
    # Most leagues the server keeps across every profile, so storage stays bounded.
    max_stored_leagues: int = field(default_factory=lambda: int(os.getenv("MAX_STORED_LEAGUES", "100000")))
    # Making or removing a league (per client IP): a burst of this many, then one every
    # 1 / LEAGUE_CHANGES_PER_SECOND seconds. Default: 12, then 4 a minute.
    league_change_burst: int = field(default_factory=lambda: int(os.getenv("LEAGUE_CHANGE_BURST", "12")))
    league_change_per_second: float = field(
        default_factory=lambda: float(os.getenv("LEAGUE_CHANGES_PER_SECOND", str(4 / 60)))
    )
    # Number of proxies in front of the app that each append to X-Forwarded-For.
    trusted_proxy_hops: int = field(default_factory=lambda: int(os.getenv("TRUSTED_PROXY_HOPS", "1")))

    # Live ESPN drafts (app/api/live_draft.py): seconds between reads of a league's draft,
    # hours an unused session (and its cookies) is kept, most sessions at once, and the
    # longest one event stream runs before the page reconnects.
    live_draft_poll_seconds: float = field(
        default_factory=lambda: float(os.getenv("LIVE_DRAFT_POLL_SECONDS", "4"))
    )
    live_draft_session_hours: float = field(
        default_factory=lambda: float(os.getenv("LIVE_DRAFT_SESSION_HOURS", "6"))
    )
    # Every session shares one ESPN rate (requests per second, across all leagues), so keep
    # LIVE_DRAFT_MAX_SESSIONS at or below LIVE_DRAFT_ESPN_REQUESTS_PER_SECOND x
    # LIVE_DRAFT_POLL_SECONDS, or reads queue up and the feed falls behind.
    live_draft_espn_requests_per_second: float = field(
        default_factory=lambda: float(os.getenv("LIVE_DRAFT_ESPN_REQUESTS_PER_SECOND", "20"))
    )
    live_draft_max_sessions: int = field(default_factory=lambda: int(os.getenv("LIVE_DRAFT_MAX_SESSIONS", "80")))
    # Connecting without a season reads last year's league before this month (1-12): ESPN
    # keeps a league under the season it started in, so in January to July a league id
    # usually belongs to the season that just ended. From this month on, this year's.
    live_draft_season_month: int = field(default_factory=lambda: int(os.getenv("LIVE_DRAFT_SEASON_MONTH", "8")))
    live_draft_stream_hours: float = field(
        default_factory=lambda: float(os.getenv("LIVE_DRAFT_STREAM_HOURS", "1"))
    )


settings = Settings()
