from datetime import datetime, timezone

from app.data import db
from app.data.sources import weather


def event(event_id, completed=False, away="WSH", home="NYG", date="2026-11-12T01:15Z", indoor=False):
    return {"id": event_id, "date": date, "competitions": [{
        "status": {"type": {"completed": completed}},
        "competitors": [{"homeAway": "home", "team": {"abbreviation": home}},
                        {"homeAway": "away", "team": {"abbreviation": away}}],
        "venue": {"fullName": "MetLife Stadium", "indoor": indoor,
                  "address": {"city": "East Rutherford", "state": "NJ", "country": "USA"}},
    }]}


def test_parse_scoreboard_skips_finished_games_and_normalizes_teams():
    data = {"week": {"number": 10}, "events": [event("1"), event("2", completed=True)]}
    (game,) = weather.parse_scoreboard_games(data)
    assert (game["espn_event_id"], game["away_team"], game["home_team"], game["week"]) == ("1", "WAS", "NYG", 10)
    assert game["kickoff_dt"] == datetime(2026, 11, 12, 1, 15, tzinfo=timezone.utc)


def test_parse_scoreboard_skips_malformed_events():
    no_venue = event("7")
    del no_venue["competitions"][0]["venue"]
    no_id = event("x")
    del no_id["id"]
    data = {"week": {"number": 10}, "events": [
        "not an event",
        {"id": "3", "date": "2026-11-12T01:15Z"},  # no competitions
        {"id": "4", "date": "2026-11-12T01:15Z", "competitions": []},
        {"id": "5", "date": "2026-11-12T01:15Z", "competitions": [{"competitors": []}]},  # no status: not final
        {**event("6"), "date": None},
        {**event("8"), "date": "next Sunday"},
        no_id,
        no_venue,
        event("1"),
    ]}
    games = weather.parse_scoreboard_games(data)
    assert [g["espn_event_id"] for g in games] == ["5", "7", "1"]
    assert games[0]["away_team"] is None and games[1]["city"] is None


def test_geocode_prefers_matching_state():
    results = [{"admin1": "Georgia", "latitude": 30.9, "longitude": -83.3},
               {"admin1": "Massachusetts", "latitude": 42.07, "longitude": -71.25}]
    assert weather.pick_geocode_result(results, "MA") == (42.07, -71.25)
    assert weather.pick_geocode_result(results, None) == (30.9, -83.3)
    assert weather.pick_geocode_result([], "MA") is None


def test_weather_row_averages_the_game_window(tmp_path):
    game = weather.parse_scoreboard_games({"week": {"number": 10}, "events": [event("1")]})[0]
    hours = ["2026-11-12T00:00", "2026-11-12T01:00", "2026-11-12T02:00", "2026-11-12T03:00", "2026-11-12T04:00"]
    forecast = {"hourly": {"time": hours, "temperature_2m": [50, 40, 38, 36, 10],
                           "wind_speed_10m": [5, 12, 15, 18, 40], "precipitation_probability": [0, 30, 60, 90, 100],
                           "precipitation": [0, 0, 0.1, 0.2, 1]}}
    row = weather.game_weather_row(game, forecast, 2026, "now")
    assert (row["temperature_f"], row["wind_mph"], row["precip_probability"]) == (38.0, 15.0, 60.0)
    assert row["precipitation_in"] == 0.1
    assert db.upsert(db.connect(tmp_path / "t.db"), "game_weather", [row]) == 1
