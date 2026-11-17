"""Game-time weather forecasts for upcoming games.

- ESPN's public scoreboard gives each game's kickoff time, venue city/state, and whether
  the stadium is indoors.
- Open-Meteo's geocoding API turns the venue city into coordinates (cached per city).
- Open-Meteo's forecast API returns hourly temperature, wind, and precipitation for every
  venue in a single request (up to 16 days ahead). No API key; free for non-commercial use,
  data under CC BY 4.0 (https://open-meteo.com).

Weather is averaged over the three hours from kickoff. Past games already have observed
temperature and wind in the nflverse schedule, so this only covers games not yet played.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.data.sources.espn import SITE_API, competitor_abbrev, is_completed, scoreboard_competition
from app.data.sources.http import RateLimitedClient

log = logging.getLogger(__name__)

GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
GAME_HOURS = 3
FORECAST_DAYS = 16

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def fetch_upcoming_weather(client: RateLimitedClient, season: int) -> list[dict]:
    """Forecasts for unplayed games in ESPN's current week and the two after it.

    Two weeks ahead, because early in the week ESPN's "current" week can still be the one
    finishing on Monday night.
    """
    current = client.get_json(f"{SITE_API}/scoreboard")
    week = (current.get("week") or {}).get("number")
    boards = [current]
    for ahead in (1, 2) if week else ():
        boards.append(client.get_json(f"{SITE_API}/scoreboard", params={"seasontype": 2, "week": week + ahead, "dates": season}))
    games = [g for board in boards for g in parse_scoreboard_games(board)]
    now = datetime.now(timezone.utc)
    games = [g for g in games if now - timedelta(hours=GAME_HOURS) < g["kickoff_dt"] < now + timedelta(days=FORECAST_DAYS - 1)]
    if not games:
        return []

    coords: dict[tuple, tuple[float, float] | None] = {}
    for g in games:
        key = (g["city"], g["state"], g["country"])
        if key not in coords:
            coords[key] = geocode(client, *key)
    located = [g for g in games if coords[(g["city"], g["state"], g["country"])]]
    if not located:
        return []

    points = [coords[(g["city"], g["state"], g["country"])] for g in located]
    forecasts = client.get_json(FORECAST_API, params={
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability,precipitation",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "forecast_days": FORECAST_DAYS,
    })
    if isinstance(forecasts, dict):  # a single location comes back as an object, not a list
        forecasts = [forecasts]
    fetched_at = now.isoformat()
    return [game_weather_row(g, f, season, fetched_at) for g, f in zip(located, forecasts)]


def parse_scoreboard_games(data: dict) -> list[dict]:
    """Unplayed games on an ESPN scoreboard. Malformed events are logged and skipped."""
    games = []
    week = (data.get("week") or {}).get("number")
    for event in data.get("events") or []:
        competition = scoreboard_competition(event)
        if competition is None or is_completed(competition):
            continue
        kickoff = event.get("date")
        try:
            kickoff_dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            log.warning("Skipping ESPN event %s with a bad kickoff time %r", event.get("id"), kickoff)
            continue
        if kickoff_dt.tzinfo is None:
            kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
        if event.get("id") is None:
            log.warning("Skipping ESPN event with no id at %s", kickoff)
            continue
        teams = {
            c.get("homeAway"): competitor_abbrev(c)
            for c in competition.get("competitors") or []
            if isinstance(c, dict)
        }
        venue = competition.get("venue") if isinstance(competition.get("venue"), dict) else {}
        address = venue.get("address") if isinstance(venue.get("address"), dict) else {}
        games.append({
            "espn_event_id": str(event["id"]),
            "week": week,
            "away_team": teams.get("away"),
            "home_team": teams.get("home"),
            "kickoff": kickoff,
            "kickoff_dt": kickoff_dt,
            "venue": venue.get("fullName"),
            "city": address.get("city"),
            "state": address.get("state"),
            "country": address.get("country"),
            "indoor": bool(venue.get("indoor")),
        })
    return games


def geocode(client: RateLimitedClient, city: str | None, state: str | None, country: str | None):
    """(lat, lon) for a venue city, preferring a match in the given US state."""
    if not city:
        return None
    us = country in (None, "USA", "United States")
    params = {"name": city, "count": 10, "language": "en", "format": "json"}
    if us:
        params["countryCode"] = "US"
    results = client.get_json(GEOCODE_API, params=params).get("results") or []
    return pick_geocode_result(results, state if us else None)


def pick_geocode_result(results: list[dict], state: str | None):
    if not results:
        return None
    if state:
        state_name = US_STATES.get(state, state)
        for r in results:
            if r.get("admin1") == state_name:
                return r["latitude"], r["longitude"]
    return results[0]["latitude"], results[0]["longitude"]


def game_weather_row(game: dict, forecast: dict, season: int, fetched_at: str) -> dict:
    """Average the forecast over the game window (kickoff hour plus the next GAME_HOURS - 1)."""
    hourly = forecast.get("hourly") or {}
    start = game["kickoff_dt"].replace(minute=0, second=0, microsecond=0)
    window = {(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(GAME_HOURS)}
    idx = [i for i, t in enumerate(hourly.get("time", [])) if t in window]

    def avg(field):
        values = [hourly[field][i] for i in idx if hourly.get(field) and hourly[field][i] is not None]
        return round(sum(values) / len(values), 2) if values else None

    return {
        "espn_event_id": game["espn_event_id"],
        "season": season,
        "week": game["week"],
        "away_team": game["away_team"],
        "home_team": game["home_team"],
        "kickoff": game["kickoff"],
        "venue": game["venue"],
        "city": game["city"],
        "indoor": int(game["indoor"]),
        "temperature_f": avg("temperature_2m"),
        "wind_mph": avg("wind_speed_10m"),
        "precip_probability": avg("precipitation_probability"),
        "precipitation_in": avg("precipitation"),
        "fetched_at": fetched_at,
    }
