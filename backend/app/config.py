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
    # Minimum seconds between requests to the same external host (rate limiting).
    request_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("REQUEST_INTERVAL_SECONDS", "1.0"))
    )


settings = Settings()
