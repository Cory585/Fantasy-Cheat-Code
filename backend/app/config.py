"""Application settings. Extend with env-driven config (API keys, DB URLs) as needed."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "Fantasy Cheat Code API"
    version: str = "0.1.0"
    # Default league scoring format used when a request doesn't specify one.
    default_scoring: str = "ppr"
    # A trade is considered "fair" if value difference is within this percentage.
    fair_trade_threshold_pct: float = 10.0


settings = Settings()
