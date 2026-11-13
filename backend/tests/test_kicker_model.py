import json

import numpy as np
import pytest

from app.kicker.features import EXPECTED_SIGNS, FEATURES, KickerFeatureBuilder
from app.kicker.train import backtest
from app.services.outlook import kicker_points
from tests.league_fixture import build_league


def rows_by_key(builder, seasons=(2025,)):
    return {(r.season, r.week, r.defense): r for r in builder.rows(list(seasons), include_unplayed=True)}


@pytest.fixture
def conn(tmp_path):
    return build_league(tmp_path / "league.db")


def test_every_feature_has_an_expected_sign():
    assert set(EXPECTED_SIGNS) == set(FEATURES)


def test_rows_have_every_feature_and_the_teams_kicker_points(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    buf = rows[(2025, 4, "BUF")]  # the row's `defense` field is the kicking team here
    assert set(buf.features) == set(FEATURES)
    assert all(np.isfinite(v) for v in buf.features.values())
    stats = json.loads(conn.execute("SELECT stats FROM team_week_stats WHERE team = 'BUF' AND season = 2025 "
                                    "AND week = 4").fetchone()[0])
    assert buf.target == kicker_points(stats)


def test_stronger_offense_projects_more_kicking(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    buf, nyj = rows[(2025, 4, "BUF")], rows[(2025, 4, "NYJ")]
    for name in ("team_implied_points", "team_fg_att_pg", "team_k_ppg", "exp_k_points"):
        assert buf.features[name] > nyj.features[name], name


def test_fg_pct_is_shrunk_toward_the_league_rate(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    # Every team misses 1 of (fg + 2) attempts a game, so the raw rate is below 85%... but the
    # 20-attempt prior keeps an early-season estimate close to the league rate.
    early, late = rows[(2025, 1, "NYJ")].features["fg_pct"], rows[(2025, 6, "NYJ")].features["fg_pct"]
    assert 0.6 < early < 0.85 and late <= early


def test_weather_and_venue(tmp_path):
    rows = rows_by_key(KickerFeatureBuilder(build_league(tmp_path / "dome.db", roof="dome")))
    assert rows[(2025, 4, "BUF")].features["indoor"] == 1.0
    assert rows[(2025, 4, "BUF")].features["wind_mph"] == 0.0
    outdoors = rows_by_key(KickerFeatureBuilder(build_league(tmp_path / "out.db")))
    assert outdoors[(2025, 4, "BUF")].features["indoor"] == 0.0
    assert outdoors[(2025, 4, "BUF")].features["wind_mph"] == 5.0


def test_features_only_use_earlier_weeks(conn):
    before = rows_by_key(KickerFeatureBuilder(conn))
    stats = json.loads(conn.execute("SELECT stats FROM team_week_stats WHERE team = 'BUF' AND season = 2025 "
                                    "AND week = 4").fetchone()[0])
    stats["fg_made_50_59"] = 3
    stats["fg_att"] += 3
    conn.execute("UPDATE team_week_stats SET stats = ? WHERE team = 'BUF' AND season = 2025 AND week = 4",
                 (json.dumps(stats),))
    conn.commit()
    after = rows_by_key(KickerFeatureBuilder(conn))
    assert after[(2025, 4, "BUF")].features == before[(2025, 4, "BUF")].features
    assert after[(2025, 4, "BUF")].target == before[(2025, 4, "BUF")].target + 15
    assert after[(2025, 5, "BUF")].features["long_fg_share"] > before[(2025, 5, "BUF")].features["long_fg_share"]
    # BUF kicked against MIA in week 4, so MIA now allows more: in week 5 NYJ kicks against MIA.
    assert after[(2025, 5, "NYJ")].opponent == "MIA"
    assert (after[(2025, 5, "NYJ")].features["opp_k_allowed_pg"]
            > before[(2025, 5, "NYJ")].features["opp_k_allowed_pg"])


def test_backtest_reports_the_model_and_baselines(tmp_path):
    conn = build_league(tmp_path / "league.db", seasons=(2023, 2024, 2025))
    rows = [r for r in KickerFeatureBuilder(conn).rows([2024, 2025]) if r.target is not None]
    result = backtest(rows, 2025)
    assert result["train_seasons"] == [2024]
    assert set(result["baselines"]) == {"league_average", "team_season_average", "ppg_times_matchup", "vegas_only"}
    assert result["model"]["n"] == len([r for r in rows if r.season == 2025])
