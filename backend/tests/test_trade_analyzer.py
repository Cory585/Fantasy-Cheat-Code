import pytest

from app.data.repository import InMemoryPlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.models.trade import LeagueSettings, PlayerValuation, ScoringFormat, TradeRequest, TradeSide, Verdict
from app.services.trade_analyzer import ScoringMismatchError, TradeAnalyzer, UnknownPlayerError
from app.services.valuation import ValuationService


@pytest.fixture
def analyzer() -> TradeAnalyzer:
    repo = InMemoryPlayerRepository(SAMPLE_PLAYERS)
    return TradeAnalyzer(repo, ValuationService(repo))


def trade(a: list[str], b: list[str]) -> TradeRequest:
    return TradeRequest(side_a=TradeSide(player_ids=a), side_b=TradeSide(player_ids=b))


def test_even_trade_is_fair(analyzer):
    result = analyzer.analyze(trade(["rb1"], ["wr1"]))
    assert result.verdict == Verdict.FAIR


def test_lopsided_trade_favors_receiver(analyzer):
    # Side A gives a lesser TE and receives a top RB.
    result = analyzer.analyze(trade(["te2"], ["rb1"]))
    assert result.verdict == Verdict.FAVORS_A
    assert result.side_a.net_value > 0
    assert result.side_b.net_value < 0


def test_uneven_player_count_warns(analyzer):
    result = analyzer.analyze(trade(["rb2", "wr3"], ["rb1"]))
    assert any("Uneven" in w for w in result.warnings)


def test_injured_player_is_discounted_and_warned(analyzer):
    result = analyzer.analyze(trade(["rb3"], ["wr3"]))
    assert result.side_a.gives[0].value < 120.0
    assert any("Q" in w for w in result.warnings)


def test_unknown_player_raises(analyzer):
    with pytest.raises(UnknownPlayerError):
        analyzer.analyze(trade(["nope"], ["rb1"]))


def test_same_player_on_both_sides_raises(analyzer):
    with pytest.raises(ValueError):
        analyzer.analyze(trade(["rb1"], ["rb1"]))


def test_same_player_twice_on_one_side_raises(analyzer):
    # Listing a player twice must not double-count their value.
    with pytest.raises(ValueError):
        analyzer.analyze(trade(["rb1", "rb1"], ["wr1"]))


@pytest.mark.parametrize("a_total,b_total", [(3.1, 2.79), (100.0, 89.99)])
def test_verdict_matches_reported_difference_at_threshold(a_total, b_total):
    # An exact 10% gap computes as 10.000000000000002; 10.01% is reported as 10.0%.
    # Both are reported as 10.0% and must be judged fair (threshold is inclusive).
    diff = round(TradeAnalyzer._difference_pct(a_total, b_total), 1)
    assert diff == 10.0
    assert TradeAnalyzer._verdict(a_total, b_total, diff) == Verdict.FAIR


def test_reported_difference_drives_verdict(analyzer, monkeypatch):
    values = {"rb1": 100.0, "wr1": 89.99}
    monkeypatch.setattr(
        analyzer._valuation,
        "value",
        lambda p, league: PlayerValuation(player=p, value=values[p.id], projection=p.ros_projection),
    )
    result = analyzer.analyze(trade(["rb1"], ["wr1"]))
    assert result.value_difference_pct == 10.0
    assert result.verdict == Verdict.FAIR


def test_balance_suggests_players_near_the_gap_for_the_winner(analyzer):
    # Side A gives te2 (85) and receives rb1 (180): A wins, so A should add ~95 of value.
    result = analyzer.analyze(trade(["te2"], ["rb1"]))
    assert result.balance.side == "a"
    assert result.balance.value_gap == 95.0
    suggested = [v.player.id for v in result.balance.players]
    assert "te2" not in suggested and "rb1" not in suggested
    gaps = [abs(v.value - 95.0) for v in result.balance.players]
    assert gaps == sorted(gaps)


def test_balance_uses_winners_roster_when_given(analyzer):
    request = TradeRequest(
        side_a=TradeSide(player_ids=["te2"], roster_ids=["te2", "k1", "dst1"]),
        side_b=TradeSide(player_ids=["rb1"]),
    )
    result = analyzer.analyze(request)
    assert {v.player.id for v in result.balance.players} == {"k1", "dst1"}


def test_no_balance_for_fair_trade(analyzer):
    assert analyzer.analyze(trade(["rb1"], ["wr1"])).balance is None


def test_scoring_must_match_the_synced_data():
    repo = InMemoryPlayerRepository(SAMPLE_PLAYERS)
    synced_ppr = TradeAnalyzer(repo, ValuationService(repo), scoring=ScoringFormat.PPR)
    request = trade(["rb1"], ["wr1"])
    assert synced_ppr.analyze(request)  # no scoring given: the synced data's
    request.league = LeagueSettings(scoring=ScoringFormat.PPR)
    assert synced_ppr.analyze(request)
    request.league = LeagueSettings(scoring=ScoringFormat.HALF_PPR)
    with pytest.raises(ScoringMismatchError, match="synced for PPR"):
        synced_ppr.analyze(request)


def test_sample_data_accepts_any_scoring(analyzer):
    request = trade(["rb1"], ["wr1"])
    request.league = LeagueSettings(scoring=ScoringFormat.STANDARD)
    assert analyzer.analyze(request)
