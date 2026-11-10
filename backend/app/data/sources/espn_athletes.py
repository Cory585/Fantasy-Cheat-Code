"""ESPN athlete profiles: bio, NFL career stats, and college stats.

These are the endpoints ESPN's own player pages call. They're undocumented and need no
auth. ESPN uses one athlete id across leagues, so a player's NFL `espn_id` also returns
his college stats from the college-football endpoint.

Responses are treated as untrusted input: only the fields used are read, each one is
type-checked and length-capped, and URLs are only accepted from ESPN's image CDN. A
missing or malformed field becomes None rather than an error.
"""

import httpx

from app.data.sources.http import RateLimitedClient

ATHLETE_API = "https://site.web.api.espn.com/apis/common/v3/sports/football"
LEAGUES = {"nfl": "nfl", "college": "college-football"}
HEADSHOT_PREFIX = "https://a.espncdn.com/"
MAX_ESPN_ID = 10**10

MAX_TEXT = 120
MAX_CATEGORIES = 12
MAX_COLUMNS = 24
MAX_SEASONS = 30


class AthleteNotFound(Exception):
    """ESPN has no athlete (or no stats in that league) for this id."""


def valid_espn_id(espn_id) -> bool:
    return isinstance(espn_id, int) and not isinstance(espn_id, bool) and 0 < espn_id < MAX_ESPN_ID


def headshot_url(espn_id: int) -> str:
    return f"{HEADSHOT_PREFIX}i/headshots/nfl/players/full/{espn_id}.png"


class EspnAthleteSource:
    def __init__(self, client: RateLimitedClient):
        self._client = client

    def _get(self, path: str) -> dict:
        try:
            data = self._client.get_json(f"{ATHLETE_API}/{path}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise AthleteNotFound(path) from error
            raise
        if not isinstance(data, dict):
            raise ValueError("Unexpected response shape from ESPN")
        return data

    def bio(self, espn_id: int) -> dict:
        if not valid_espn_id(espn_id):
            raise ValueError("Invalid ESPN id")
        return parse_bio(self._get(f"nfl/athletes/{espn_id}"), espn_id)

    def stats(self, espn_id: int, league: str) -> list[dict]:
        if not valid_espn_id(espn_id) or league not in LEAGUES:
            raise ValueError("Invalid ESPN id or league")
        return parse_stats(self._get(f"{LEAGUES[league]}/athletes/{espn_id}/stats"))


# ---------- parsing helpers ----------


def _text(value, limit: int = MAX_TEXT) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())  # collapse whitespace and control characters
    return value[:limit] or None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _number(value: str | None) -> float | None:
    """ESPN stat strings like "4,427" or "5.2"."""
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


# ---------- parsers ----------


def parse_bio(data: dict, espn_id: int) -> dict:
    athlete = _dict(data.get("athlete"))
    if not athlete:
        raise AthleteNotFound(str(espn_id))
    headshot = _text(_dict(athlete.get("headshot")).get("href"), 300)
    if not headshot or not headshot.startswith(HEADSHOT_PREFIX):
        headshot = headshot_url(espn_id)
    return {
        "espn_id": espn_id,
        "name": _text(athlete.get("displayName")),
        "position": _text(_dict(athlete.get("position")).get("abbreviation"), 8),
        "team": _text(_dict(athlete.get("team")).get("abbreviation"), 8),
        "jersey": _text(athlete.get("displayJersey") or athlete.get("jersey"), 8),
        "height": _text(athlete.get("displayHeight"), 16),
        "weight": _text(athlete.get("displayWeight"), 16),
        "age": _int(athlete.get("age")),
        "birth_place": _text(athlete.get("displayBirthPlace")),
        "experience": _text(athlete.get("displayExperience"), 40),
        "college": _text(_dict(athlete.get("college")).get("name")),
        "draft": _text(athlete.get("displayDraft")),
        "status": _text(_dict(athlete.get("status")).get("name"), 40),
        "headshot_url": headshot,
    }


def parse_stats(data: dict) -> list[dict]:
    """Stat categories (rushing, receiving, ...), each with one row per season and totals."""
    teams = {slug: _text(_dict(team).get("abbreviation"), 8) for slug, team in _dict(data.get("teams")).items()}
    categories = []
    for category in _list(data.get("categories"))[:MAX_CATEGORIES]:
        category = _dict(category)
        labels = [_text(label, 12) or "" for label in _list(category.get("labels"))[:MAX_COLUMNS]]
        names = _list(category.get("displayNames"))
        if not labels:
            continue
        columns = [
            {"label": label, "name": _text(names[i] if i < len(names) else None, 60) or label}
            for i, label in enumerate(labels)
        ]
        seasons = []
        for row in _list(category.get("statistics"))[:MAX_SEASONS]:
            row = _dict(row)
            year = _int(_dict(row.get("season")).get("year"))
            if year is None:
                continue
            values = [_number(v) for v in _list(row.get("stats"))[: len(columns)]]
            values += [None] * (len(columns) - len(values))
            seasons.append({"year": year, "team": teams.get(row.get("teamSlug")), "values": values})
        totals = [_number(v) for v in _list(category.get("totals"))[: len(columns)]]
        categories.append({
            "name": _text(category.get("name"), 40) or "stats",
            "label": _text(category.get("displayName"), 40) or "Stats",
            "columns": columns,
            "seasons": seasons,
            "totals": totals + [None] * (len(columns) - len(totals)) if totals else [],
        })
    return categories
