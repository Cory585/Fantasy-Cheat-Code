from app.data import db
from app.data.sources.playcalling import parse_play_by_play, parse_play_by_play_tables

COLUMNS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam", "play_type", "qb_dropback", "rush",
    "sack", "interception", "fumble_lost", "qb_epa", "wp", "down", "half_seconds_remaining", "pass_oe",
    "pass_attempt", "rush_attempt", "two_point_attempt", "receiver_player_id", "rusher_player_id",
    "pass_location", "air_yards", "yardline_100",
]
HEADER = ",".join(COLUMNS) + "\n"


def play(**overrides):
    base = dict(game_id="2026_01_LA_SF", season="2026", week="1", season_type="REG", posteam="LA", defteam="SF",
                play_type="pass", qb_dropback="1", rush="0", sack="0", interception="0", fumble_lost="0",
                qb_epa="0.5", wp="0.5", down="1", half_seconds_remaining="900", pass_oe="10",
                pass_attempt="1", rush_attempt="0", two_point_attempt="0", receiver_player_id="NA",
                rusher_player_id="NA", pass_location="NA", air_yards="NA", yardline_100="60")
    base.update(overrides)
    return ",".join(base[c] for c in COLUMNS) + "\n"


def test_summarizes_offense_per_game():
    text = HEADER + "".join([
        play(),
        play(sack="1", qb_epa="-1.5", pass_oe="20"),
        play(play_type="run", qb_dropback="0", rush="1", qb_epa="NA", pass_oe="-30", fumble_lost="1"),
        play(interception="1", wp="0.95", pass_oe="50"),  # not neutral (blowout)
        play(play_type="no_play"),  # penalty, ignored
        play(play_type="punt", qb_dropback="0", rush="0"),  # not a pass or rush, ignored
    ])
    (row,) = parse_play_by_play(text)
    assert row["team"] == "LAR" and row["opponent"] == "SF"  # nflverse 'LA' normalized
    assert (row["plays"], row["dropbacks"], row["sacks"], row["interceptions"], row["fumbles_lost"]) == (4, 3, 1, 1, 1)
    assert row["dropback_epa"] == 0.5 - 1.5 + 0.5
    assert (row["neutral_plays"], row["neutral_dropbacks"]) == (3, 2)
    assert row["pass_oe_sum"] == 10 + 20 - 30 and row["pass_oe_n"] == 3


def test_rows_fit_schema(tmp_path):
    rows = parse_play_by_play(HEADER + play() + play(posteam="SF", defteam="LA"))
    assert db.upsert(db.connect(tmp_path / "t.db"), "team_game_offense", rows) == 2


def target(receiver="00-001", **kw):
    return play(receiver_player_id=receiver, **kw)


def carry(rusher="00-009", **kw):
    return play(play_type="run", qb_dropback="0", rush="1", pass_attempt="0", rush_attempt="1",
                rusher_player_id=rusher, **kw)


def test_counts_each_players_usage_per_game():
    text = HEADER + "".join([
        target(pass_location="middle", air_yards="6"),
        target(pass_location="middle", air_yards="-2", down="3"),
        target(pass_location="left", air_yards="25"),
        target(pass_location="right", air_yards="12", yardline_100="10"),  # red zone, into the end zone
        target(pass_location="right", air_yards="4", yardline_100="15"),  # red zone, short of it
        target(sack="1", pass_location="middle", air_yards="5"),  # sacks have no target
        target(two_point_attempt="1", pass_location="left", air_yards="2"),  # nor do 2-point tries
        target(play_type="no_play", pass_location="left", air_yards="9"),  # penalty
        target(receiver="00-002", pass_location="left", air_yards="8"),
        carry(yardline_100="3"),
        carry(yardline_100="18"),
        carry(),
    ])
    _, usage = parse_play_by_play_tables(text)
    rows = {row["gsis_id"]: row for row in usage}
    assert set(rows) == {"00-001", "00-002", "00-009"}
    wr = rows["00-001"]
    assert wr["team"] == "LAR" and wr["week"] == 1
    assert (wr["targets"], wr["targets_left"], wr["targets_middle"], wr["targets_right"]) == (5, 1, 2, 2)
    assert wr["air_yards"] == 6 - 2 + 25 + 12 + 4
    assert (wr["deep_targets"], wr["behind_los_targets"], wr["third_down_targets"]) == (1, 1, 1)
    assert (wr["red_zone_targets"], wr["end_zone_targets"]) == (2, 1)
    assert wr["carries"] == 0
    rb = rows["00-009"]
    assert (rb["carries"], rb["red_zone_carries"], rb["goal_line_carries"], rb["targets"]) == (3, 2, 1, 0)


def test_usage_rows_fit_schema(tmp_path):
    _, usage = parse_play_by_play_tables(HEADER + target(pass_location="middle", air_yards="7") + carry())
    assert db.upsert(db.connect(tmp_path / "t.db"), "player_game_usage", usage) == 2
