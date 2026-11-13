"""Fixtures for tests against the real collected NFL data (`backend/data/fantasy.db`).

These tests check the app against things that actually happened: league leaders, famous
games, and individual seasons from 2018-2025, plus how well the DST model predicts real games
it wasn't trained on. They need history collected first:

    python -m app.data.collect --seasons 2018-2026

and are skipped when the database or a season they need is missing. Nothing here contacts an
outside provider, and the database is opened read-only.
"""

import sqlite3

import pytest

from app.config import settings
from app.defense.features import DefenseFeatureBuilder
from app.services.defense_breakdown import LeagueDefenses

FIRST_SEASON = 2018  # the earliest season these tests check (2018 is also the model's prior)
COMPLETE_SEASONS = range(2019, 2026)  # seasons the DST model can be trained and tested on


@pytest.fixture(scope="session")
def real_db():
    path = settings.database_path
    if not path.exists():
        pytest.skip(f"No collected database at {path}; run `python -m app.data.collect --seasons 2018-2026`")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def collected_seasons(real_db) -> set[int]:
    return {row[0] for row in real_db.execute("SELECT DISTINCT season FROM team_week_stats")}


@pytest.fixture(scope="session")
def need(collected_seasons):
    """need(2019, 2020) skips the test unless those seasons have team stats."""
    def check(*seasons: int) -> None:
        missing = sorted(set(seasons) - collected_seasons)
        if missing:
            pytest.skip(f"Seasons {missing} aren't collected; run `python -m app.data.collect --seasons 2018-2026`")
    return check


@pytest.fixture(scope="session")
def league(real_db, need):
    """league(2021) -> that season's LeagueDefenses (built once per season)."""
    cache: dict[int, LeagueDefenses] = {}

    def get(season: int) -> LeagueDefenses:
        need(season)
        if season not in cache:
            cache[season] = LeagueDefenses(real_db, season)
        return cache[season]
    return get


@pytest.fixture(scope="session")
def dst_builder(real_db, need):
    need(FIRST_SEASON, *COMPLETE_SEASONS)
    return DefenseFeatureBuilder(real_db, first_season=FIRST_SEASON)


@pytest.fixture(scope="session")
def dst_rows(dst_builder, collected_seasons):
    """Every played regular-season DST row from 2019 on (2018 is only the prior)."""
    seasons = sorted(s for s in collected_seasons if s > FIRST_SEASON)
    return [r for r in dst_builder.rows(seasons) if r.target is not None]
