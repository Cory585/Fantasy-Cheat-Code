import json

import numpy as np
import pytest

from app.kicker.features import EXPECTED_SIGNS, FEATURES, KickerFeatureBuilder
from app.defense.features import FeatureRow
from app.kicker.train import backtest, season_weights, train, trend_intercept
from app.services.outlook import kicker_points
from app.services.special_teams import KICKER_LABELS
from tests.league_fixture import build_league


def rows_by_key(builder, seasons=(2025,)):
    return {(r.season, r.week, r.defense): r for r in builder.rows(list(seasons), include_unplayed=True)}


@pytest.fixture
def conn(tmp_path):
    return build_league(tmp_path / "league.db")


def test_every_feature_has_an_expected_sign_and_a_label():
    assert set(EXPECTED_SIGNS) == set(FEATURES)
    assert set(KICKER_LABELS) == set(FEATURES)


def test_rows_have_every_feature_and_the_teams_kicker_points(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    buf = rows[(2025, 4, "BUF")]  # the row's `defense` field is the kicking team here
    assert set(FEATURES) <= set(buf.features)
    assert all(np.isfinite(v) for v in buf.features.values())
    stats = json.loads(conn.execute("SELECT stats FROM team_week_stats WHERE team = 'BUF' AND season = 2025 "
                                    "AND week = 4").fetchone()[0])
    assert buf.target == kicker_points(stats)


def test_stronger_offense_projects_more_kicking(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    buf, nyj = rows[(2025, 4, "BUF")], rows[(2025, 4, "NYJ")]
    for name in ("team_implied_points", "team_favored_by", "team_k_ppg"):
        assert buf.features[name] > nyj.features[name], name


def test_favored_by_is_the_spread_from_each_side(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    home = next(r for r in rows.values() if r.home and r.week == 4)
    away = rows[(2025, 4, home.opponent)]
    assert home.features["team_favored_by"] == -away.features["team_favored_by"]


def test_games_without_a_line_use_the_dst_models_line_estimate(tmp_path):
    conn = build_league(tmp_path / "league.db", unplayed_from=5)
    conn.execute("UPDATE games SET spread_line = NULL, total_line = NULL WHERE season = 2025 AND week >= 5")
    conn.commit()
    builder = KickerFeatureBuilder(conn)
    rows = rows_by_key(builder)
    dst = {(r.season, r.week, r.defense): r for r in builder.dst_rows([2025], include_unplayed=True)}
    # Week 5: BUF at NE. Without a line, BUF's kicker gets the same implied points the DST
    # model gives the offense NE's defense faces, not raw points per game.
    buf, ne = rows[(2025, 5, "BUF")], rows[(2025, 5, "NE")]
    assert buf.context["line_estimated"] and not rows[(2025, 4, "BUF")].context["line_estimated"]
    assert buf.features["team_implied_points"] == pytest.approx(dst[(2025, 5, "NE")].features["opp_implied_points"])
    assert buf.features["team_implied_points"] == pytest.approx(builder.estimated_points(2025, 5, "BUF", "NE", False))
    # The spread is estimated too, instead of a pick'em 0.
    assert buf.features["team_favored_by"] > 0 > ne.features["team_favored_by"]
    assert buf.features["team_favored_by"] == pytest.approx(-ne.features["team_favored_by"])
    assert buf.features["team_favored_by"] == pytest.approx(dst[(2025, 5, "BUF")].features["def_favored_by"])


def test_fg_pct_is_shrunk_toward_the_league_rate(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    # Every team misses 1 of (fg + 2) attempts a game, so the raw rate is below 85%... but the
    # 20-attempt prior keeps an early-season estimate close to the league rate.
    early, late = rows[(2025, 1, "NYJ")].features["fg_pct"], rows[(2025, 6, "NYJ")].features["fg_pct"]
    assert 0.6 < early < 0.85 and late <= early


def test_pat_pct_is_shrunk_toward_the_league_rate(conn):
    rows = rows_by_key(KickerFeatureBuilder(conn))
    # Every PAT in the fixture is good, so the rate climbs from the 95% prior with more kicks.
    early, late = rows[(2025, 1, "BUF")].features["pat_pct"], rows[(2025, 6, "BUF")].features["pat_pct"]
    assert 0.95 < early <= late < 1.0


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


def test_training_saves_the_line_estimate_it_used(tmp_path):
    conn = build_league(tmp_path / "league.db", seasons=(2023, 2024, 2025))
    model, backtests = train(conn, [2024, 2025])
    assert model.metadata["line_coefs"] == list(KickerFeatureBuilder(conn).line_coefs())
    assert [bt["train_seasons"] for bt in backtests] == [[2024]]


def _season_rows(means: dict[int, float]) -> list[FeatureRow]:
    return [FeatureRow(s, 1, f"{s}", None, "BUF", "MIA", True, {}, target)
            for s, m in means.items() for target in (m - 1, m + 1)]


def test_recent_seasons_weigh_more():
    rows = _season_rows({2022: 7.0, 2023: 7.0, 2024: 7.0})
    w = season_weights(rows, 2025)
    assert w[-1] == 1.0 and w[0] == pytest.approx(0.5)  # 2022 is two seasons before 2024


def test_intercept_follows_the_league_trend_with_enough_seasons():
    rising = _season_rows({2021: 7.0, 2022: 7.5, 2023: 8.0, 2024: 8.5})
    assert trend_intercept(rising, 2025) == pytest.approx(9.0)
    assert trend_intercept(rising, 2024) == pytest.approx(8.5)
    assert trend_intercept(rising[2:], 2025) is None  # three seasons: too few
