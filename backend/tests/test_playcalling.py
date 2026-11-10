from app.data import db
from app.data.sources.playcalling import parse_play_by_play

COLUMNS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam", "play_type", "qb_dropback", "rush",
    "sack", "interception", "fumble_lost", "qb_epa", "wp", "down", "half_seconds_remaining", "pass_oe",
]
HEADER = ",".join(COLUMNS) + "\n"


def play(**overrides):
    base = dict(game_id="2026_01_LA_SF", season="2026", week="1", season_type="REG", posteam="LA", defteam="SF",
                play_type="pass", qb_dropback="1", rush="0", sack="0", interception="0", fumble_lost="0",
                qb_epa="0.5", wp="0.5", down="1", half_seconds_remaining="900", pass_oe="10")
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
