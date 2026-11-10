"""Build a player profile from ESPN (cached), nflverse bio data, and combine results."""

import logging
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.data.sources.espn_athletes import HEADSHOT_PREFIX, headshot_url, valid_espn_id
from app.models.player import Player, Position
from app.models.profile import Bio, Combine, CombineDrill, PlayerProfile, SourceStatus, StatCategory
from app.services.profiles import ProfileService, ProfileUnavailable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Drill:
    key: str
    label: str
    unit: str
    lower_is_better: bool


DRILLS = [
    Drill("forty", "40-yard dash", "s", True),
    Drill("vertical", "Vertical jump", "in", False),
    Drill("broad_jump", "Broad jump", "in", False),
    Drill("bench", "Bench press", "reps", False),
    Drill("cone", "3-cone drill", "s", True),
    Drill("shuttle", "20-yard shuttle", "s", True),
]


def format_height(inches: float | None) -> str | None:
    if not isinstance(inches, (int, float)) or not math.isfinite(inches) or inches <= 0:
        return None
    feet, rest = divmod(round(inches), 12)
    return f"{feet}' {rest}\""


def percentile(value: float, others: list[float], lower_is_better: bool) -> int | None:
    """Share of results this one beats, counting ties as half (0-100)."""
    if len(others) < 2:
        return None
    beats = sum(1 for o in others if (o > value if lower_is_better else o < value))
    ties = sum(1 for o in others if o == value) - 1  # not counting itself
    return round(100 * (beats + 0.5 * max(ties, 0)) / (len(others) - 1))


class LocalPlayerData:
    """nflverse bio and combine results from `fantasy.db`, opened read-only per call."""

    def __init__(self, database_path: Path | None):
        self._path = database_path

    def _connect(self) -> sqlite3.Connection | None:
        if not self._path or not self._path.exists():
            return None
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn

    def lookup(self, espn_id: int | None) -> tuple[dict | None, Combine | None]:
        if not valid_espn_id(espn_id):
            return None, None
        try:
            conn = self._connect()
            if conn is None:
                return None, None
            try:
                row = conn.execute(
                    "SELECT * FROM players WHERE espn_id = ? ORDER BY rookie_season DESC LIMIT 1", (espn_id,)
                ).fetchone()
                bio = dict(row) if row else None
                combine = self._combine(conn, bio["pfr_id"]) if bio and bio.get("pfr_id") else None
                return bio, combine
            finally:
                conn.close()
        except sqlite3.Error as error:  # missing tables, locked or corrupt file: profile still works
            log.warning("Couldn't read local player data: %s", error)
            return None, None

    def _combine(self, conn: sqlite3.Connection, pfr_id: str) -> Combine | None:
        row = conn.execute("SELECT * FROM combine WHERE pfr_id = ? ORDER BY season DESC LIMIT 1", (pfr_id,)).fetchone()
        if row is None:
            return None
        drills = []
        for drill in DRILLS:
            value = row[drill.key]
            if value is None or not math.isfinite(value):
                continue
            # Column names come from the fixed DRILLS list, never from input.
            others = [r[0] for r in conn.execute(
                f"SELECT {drill.key} FROM combine WHERE position = ? AND {drill.key} IS NOT NULL", (row["position"],)
            )]
            drills.append(CombineDrill(
                key=drill.key, label=drill.label, value=value, unit=drill.unit,
                lower_is_better=drill.lower_is_better,
                percentile=percentile(value, others, drill.lower_is_better), compared_with=len(others),
            ))
        return Combine(season=row["season"], school=row["school"], position=row["position"],
                       height_in=row["height_in"], weight_lb=row["weight_lb"], drills=drills)


def _local_bio(row: dict | None, combine: Combine | None) -> Bio:
    row = row or {}
    draft = None
    if row.get("draft_year") and row.get("draft_round") and row.get("draft_pick"):
        draft = f"{row['draft_year']}: Rd {row['draft_round']}, Pk {row['draft_pick']}"
    weight = row.get("weight")
    return Bio(
        height=format_height(row.get("height")),
        weight=f"{round(weight)} lbs" if isinstance(weight, (int, float)) and math.isfinite(weight) and weight > 0 else None,
        college=combine.school if combine else None,
        draft=draft,
    )


def _categories(raw: list) -> list[StatCategory]:
    categories = []
    for item in raw if isinstance(raw, list) else []:
        try:
            categories.append(StatCategory.model_validate(item))
        except ValidationError:
            continue  # skip a malformed cached category rather than failing the profile
    return categories


def build_profile(player: Player, service: ProfileService, local: LocalPlayerData) -> PlayerProfile:
    nflverse_row, combine = local.lookup(player.espn_id)
    bio = _local_bio(nflverse_row, combine)
    nfl_stats: list[StatCategory] = []
    college_stats: list[StatCategory] = []
    headshot = headshot_url(player.espn_id) if valid_espn_id(player.espn_id) else None

    if player.position == Position.DST or not valid_espn_id(player.espn_id):
        espn = SourceStatus(status="not_applicable")
    else:
        try:
            result = service.get(player.espn_id)
        except ProfileUnavailable as error:
            code = "upstream_busy" if error.reason == "busy" else "upstream_error"
            espn = SourceStatus(status="unavailable", code=code, retry_after=max(1, math.ceil(error.retry_after)))
        else:
            cached = result.profile
            if cached.status == "not_found":
                espn = SourceStatus(status="not_found", code="espn_player_not_found", fetched_at=cached.fetched_at)
            else:
                espn = SourceStatus(status=result.freshness, fetched_at=cached.fetched_at)
                espn_bio = cached.bio or {}
                # ESPN values win; nflverse fills anything ESPN left blank.
                merged = {k: v for k, v in espn_bio.items() if k in Bio.model_fields and v is not None}
                bio = Bio(**{**bio.model_dump(), **merged})
                url = espn_bio.get("headshot_url")
                if isinstance(url, str) and url.startswith(HEADSHOT_PREFIX):
                    headshot = url
                nfl_stats = _categories(cached.nfl_stats)
                college_stats = _categories(cached.college_stats)

    return PlayerProfile(player=player, headshot_url=headshot, bio=bio, nfl_stats=nfl_stats,
                         college_stats=college_stats, combine=combine, espn=espn)
