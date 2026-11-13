from fastapi.testclient import TestClient

from app.main import app
from app.models.player import Player, PlayerOutlook, Position
from app.models.trade import Effect, PlayerValuation
from app.services.trade_reasons import side_reasons

client = TestClient(app)


def val(pid, pos, value, *, tier=None, starter=True, injury=None, sos=None, form=None, concerns=()):
    outlook = None
    if sos is not None or form is not None:
        outlook = PlayerOutlook(provider_projection=100, adjusted_projection=100, games_remaining=8,
                                sos_factor=sos, recent_ppg=12.0 if form else None, recent_games=4,
                                form_projection=form)
    player = Player(id=pid, name=pid.title(), position=pos, team="KC", injury_status=injury)
    return PlayerValuation(player=player, value=value, projection=value, outlook=outlook, tier=tier,
                           starter=starter, concerns=list(concerns))


def by_kind(reasons):
    out = {}
    for r in reasons:
        out.setdefault(r.kind, []).append(r)
    return out


def test_one_for_one_compares_value_only_once():
    stud, other = val("stud", Position.RB, 90, tier="RB1"), val("other", Position.RB, 60, tier="RB2")
    reasons = side_reasons(sends=[other], receives=[stud])
    kinds = by_kind(reasons)
    assert kinds["value"][0].effect == Effect.PLUS
    assert kinds["value"][0].text == "Receives 90 points of value for 60 sent (+30)"
    assert "best_player" not in kinds and "roster_spots" not in kinds  # nothing more to say in a 1-for-1
    assert "positions" not in kinds  # same position both ways: the value line already says it


def test_two_for_one_trade_explains_both_sides():
    stud = val("stud", Position.RB, 100, tier="RB1")
    wr, rb = val("wr", Position.WR, 70, tier="WR2"), val("rb", Position.RB, 50, tier="RB3", starter=False)
    getting_depth = by_kind(side_reasons(sends=[stud], receives=[wr, rb]))
    getting_stud = by_kind(side_reasons(sends=[wr, rb], receives=[stud]))

    assert getting_depth["value"][0].effect == Effect.PLUS  # 120 for 100
    assert getting_depth["best_player"][0].effect == Effect.MINUS
    assert getting_depth["best_player"][0].text == "Gives up the best player in the deal: Stud (RB1, 100 value)"
    assert getting_depth["roster_spots"][0].effect == Effect.MINUS
    assert "1 player must be dropped" in getting_depth["roster_spots"][0].text

    assert getting_stud["value"][0].effect == Effect.MINUS
    assert getting_stud["best_player"][0].effect == Effect.PLUS
    assert getting_stud["roster_spots"][0].text == "Consolidates 2 players into 1, opening 1 roster spot for a waiver pickup"
    # One starter (the WR) goes out and one comes back, so starters aren't a reason either way.
    assert "starters" not in getting_stud


def test_positions_starters_and_even_value():
    a = [val("rb", Position.RB, 80), val("te", Position.TE, 20, starter=False)]
    b = [val("wr", Position.WR, 60), val("qb", Position.QB, 40)]
    reasons = by_kind(side_reasons(sends=a, receives=b))
    assert reasons["value"][0].effect == Effect.NEUTRAL
    assert reasons["value"][0].text == "Sends and receives the same value (100 points)"
    assert reasons["starters"][0].effect == Effect.PLUS  # 2 starters in, 1 out
    texts = [r.text for r in reasons["positions"]]
    assert texts == ["Stronger at WR: +60 points of value", "Thinner at RB: −80 points of value"]


def test_schedule_form_injury_and_uncertainty():
    hot = val("hot", Position.WR, 60, sos=1.10, form=130)
    shaky = val("shaky", Position.RB, 30, injury="Q", concerns=["Projections disagree (ESPN 140 vs Sleeper 100)"])
    cold = val("cold", Position.WR, 80, sos=0.95, form=70)
    reasons = by_kind(side_reasons(sends=[cold], receives=[hot, shaky]))
    assert reasons["schedule"][0].effect == Effect.PLUS
    assert "easier remaining schedules (15% more points allowed" in reasons["schedule"][0].text
    assert [r.text for r in reasons["form"]] == ["Gets Hot, whose recent form points above projections"]
    assert reasons["injury"][0].text == "Takes on injury risk: Shaky (Q)"
    assert reasons["uncertainty"][0].effect == Effect.MINUS
    # And the other side sees the same facts the other way round.
    other = by_kind(side_reasons(sends=[hot, shaky], receives=[cold]))
    assert other["schedule"][0].effect == Effect.MINUS
    assert [r.text for r in other["form"]] == ["Gives up Hot, whose recent form points above projections",
                                               "Gets Cold, whose recent form points below projections"]
    assert other["injury"][0].text == "Moves injury risk: Shaky (Q)"


def test_reasons_are_capped():
    sends = [val(f"s{i}", Position.WR, 10, injury="Q") for i in range(5)]
    receives = [val("big", Position.RB, 100, injury="Q", sos=1.2, form=150,
                    concerns=["Projections disagree (x)"])]
    assert len(side_reasons(sends, receives)) <= 10


def test_trade_api_returns_reasons_and_player_breakdowns():
    body = client.post("/trades/analyze", json={
        "side_a": {"player_ids": ["rb1"]},
        "side_b": {"player_ids": ["wr2", "rb3"]},
        "league": {"num_teams": 2},
    }).json()
    a, b = body["side_a"], body["side_b"]
    assert a["reasons"][0] == {"kind": "value", "effect": "plus",
                               "text": "Receives 268 points of value for 180 sent (+88)"}
    assert b["reasons"][0]["effect"] == "minus"
    assert any(r["kind"] == "best_player" and r["effect"] == "plus" for r in b["reasons"])
    rb1 = a["gives"][0]
    assert (rb1["position_rank"], rb1["tier"], rb1["starter"]) == (1, "RB1", True)
    assert rb1["strengths"][0] == "Top-3 RB: 1st at RB by rest-of-season projection"
    rb3 = next(v for v in b["gives"] if v["player"]["id"] == "rb3")
    assert "Listed Q: trade value cut to 90%" in rb3["concerns"]
