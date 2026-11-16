from app.data.repository import InMemoryPlayerRepository
from app.models.player import Player, Position
from app.models.trade import LeagueSettings
from app.services.valuation import ValuationService


def make(pos: Position, *projections: float) -> list[Player]:
    return [
        Player(id=f"{pos.value}{i}", name=f"{pos.value} {i}", position=pos, team="KC", ros_projection=p)
        for i, p in enumerate(projections)
    ]


def service(players: list[Player]) -> ValuationService:
    return ValuationService(InMemoryPlayerRepository(players))


def test_replacement_without_flex_is_first_non_starter():
    league = LeagueSettings(num_teams=2, roster_slots={"RB": 1, "WR": 1})
    svc = service(make(Position.RB, 100, 90, 80) + make(Position.WR, 95, 85, 75))
    assert svc.replacement_level(Position.RB, league) == 80
    assert svc.replacement_level(Position.WR, league) == 75


def test_flex_slots_go_to_best_remaining_rb_wr_te():
    # 2 teams, 1 RB + 1 WR + 1 FLEX each. After dedicated slots, the next-best are
    # WR 85 and WR 75 (RB 70 is worse), so both flex spots go to WRs.
    league = LeagueSettings(num_teams=2, roster_slots={"RB": 1, "WR": 1, "FLEX": 1})
    svc = service(make(Position.RB, 100, 90, 70, 60) + make(Position.WR, 95, 92, 85, 75, 50))
    assert svc.replacement_level(Position.WR, league) == 50
    assert svc.replacement_level(Position.RB, league) == 70


def test_flex_does_not_affect_qb_or_kicker():
    league = LeagueSettings(num_teams=1, roster_slots={"QB": 1, "RB": 1, "FLEX": 1})
    svc = service(make(Position.QB, 300, 250) + make(Position.RB, 100, 90, 80))
    assert svc.replacement_level(Position.QB, league) == 250
    assert svc.replacement_level(Position.RB, league) == 80


def test_superflex_goes_to_the_best_remaining_qb_rb_wr_or_te():
    # 2 teams, 1 QB + 1 RB + 1 SUPERFLEX each. After the dedicated slots, QBs 280 and 260
    # beat the next RB (80), so both superflex spots go to QBs and the QB baseline drops.
    league = LeagueSettings(num_teams=2, roster_slots={"QB": 1, "RB": 1, "SUPERFLEX": 1})
    svc = service(make(Position.QB, 300, 290, 280, 260, 200) + make(Position.RB, 100, 90, 80, 70))
    assert svc.replacement_level(Position.QB, league) == 200
    assert svc.replacement_level(Position.RB, league) == 80
    one_qb = LeagueSettings(num_teams=2, roster_slots={"QB": 1, "RB": 1})
    assert svc.replacement_level(Position.QB, one_qb) == 280
    qb = svc.value(svc._repo.get("QB2"), league)
    assert qb.value == 80 and qb.starter  # 280 - 200: a starter only in superflex


def test_superflex_fills_after_flex():
    # FLEX takes the best RB/WR/TE first; SUPERFLEX then takes the next best of anyone.
    league = LeagueSettings(num_teams=1, roster_slots={"QB": 1, "RB": 1, "FLEX": 1, "SUPERFLEX": 1})
    svc = service(make(Position.QB, 300, 100, 50) + make(Position.RB, 200, 150, 120, 10))
    assert svc.replacement_level(Position.RB, league) == 10  # RB 150 at flex, RB 120 at superflex
    assert svc.replacement_level(Position.QB, league) == 100


def test_small_pool_gets_zero_baseline():
    league = LeagueSettings(num_teams=12)
    svc = service(make(Position.RB, 100, 90))
    assert svc.replacement_level(Position.RB, league) == 0.0
    assert svc.replacement_level(Position.TE, league) == 0.0  # empty pool


def test_value_floors_at_zero_and_discounts_injuries():
    league = LeagueSettings(num_teams=1, roster_slots={"RB": 1})
    players = make(Position.RB, 100, 50, 40)
    players[0].injury_status = "Q"
    svc = service(players)
    assert svc.value(players[0], league).value == 45.0  # (100 - 50) x 0.9
    assert svc.value(players[2], league).value == 0.0


def test_outlook_adjusted_projection_drives_value_and_replacement():
    from app.models.player import PlayerOutlook

    league = LeagueSettings(num_teams=1, roster_slots={"RB": 1})
    players = make(Position.RB, 100, 90, 80)

    def outlook(adjusted, provider, **kw):
        return PlayerOutlook(provider_projection=provider, adjusted_projection=adjusted, games_remaining=8, **kw)

    # Adjusted: RB0 100 (no outlook), RB1 70, RB2 85. The starter is RB0 and the
    # replacement level is the best non-starter by adjusted projection: RB2 at 85.
    outlooks = {"RB1": outlook(70, 90), "RB2": outlook(85, 80)}
    svc = ValuationService(InMemoryPlayerRepository(players), outlooks)
    assert svc.replacement_level(Position.RB, league) == 85
    result = svc.value(players[0], league)
    assert result.projection == 100 and result.value == 15
    assert svc.value(players[1], league).projection == 70


def test_outlook_notes_for_schedule_and_form():
    from app.models.player import PlayerOutlook
    from app.services.valuation import outlook_notes

    p = make(Position.RB, 100)[0]
    easy = PlayerOutlook(provider_projection=100, adjusted_projection=104, games_remaining=8,
                         sos_rank=2, sos_teams=32, recent_ppg=15.0, recent_games=4, form_projection=120)
    notes = outlook_notes(p, easy)
    assert "2nd easiest remaining schedule for RBs" in notes
    assert any("running above projections" in n for n in notes)
    hard = PlayerOutlook(provider_projection=100, adjusted_projection=100, games_remaining=8, sos_rank=31, sos_teams=32)
    assert outlook_notes(p, hard) == ["2nd hardest remaining schedule for RBs"]


def test_rank_phrase_for_first_place():
    from app.models.player import PlayerOutlook
    from app.services.valuation import outlook_notes

    p = make(Position.WR, 100)[0]
    o = PlayerOutlook(provider_projection=100, adjusted_projection=100, games_remaining=8, sos_rank=32, sos_teams=32)
    assert outlook_notes(p, o) == ["Hardest remaining schedule for WRs"]


# ---------- player breakdown: rank, tier, strengths, concerns ----------

def test_rank_tier_and_starter_status():
    league = LeagueSettings(num_teams=2, roster_slots={"RB": 2})
    rbs = make(Position.RB, 200, 180, 160, 140, 120, 100)
    svc = service(rbs)
    top, fourth, fifth = (svc.value(rbs[i], league) for i in (0, 3, 4))
    assert (top.position_rank, top.tier, top.starter) == (1, "RB1", True)
    assert (fourth.position_rank, fourth.tier, fourth.starter) == (4, "RB2", True)
    assert (fifth.position_rank, fifth.tier, fifth.starter) == (5, "RB3", False)
    assert top.strengths[0] == "Top-3 RB: 1st at RB by rest-of-season projection"
    assert fourth.strengths[0] == "RB2 who starts in a 2-team league (4th at RB by rest-of-season projection)"
    assert any("better than a replacement-level RB" in s for s in top.strengths)
    assert fifth.concerns[0].startswith("Below the starting line in a 2-team league (5th at RB")
    assert any("No value over replacement" in c for c in fifth.concerns)


def test_schedule_form_injury_and_provider_traits():
    from app.models.player import PlayerOutlook

    league = LeagueSettings(num_teams=1, roster_slots={"WR": 1})
    hot = Player(id="hot", name="Hot", position=Position.WR, team="KC", ros_projection=150,
                 projection_sources={"sleeper": 148, "espn": 152})
    hurt = Player(id="hurt", name="Hurt", position=Position.WR, team="KC", ros_projection=120,
                  injury_status="Q", projection_sources={"sleeper": 100, "espn": 140})
    spare = Player(id="spare", name="Spare", position=Position.WR, team="KC", ros_projection=60)
    outlooks = {
        "hot": PlayerOutlook(provider_projection=150, adjusted_projection=160, games_remaining=8, sos_rank=1,
                             sos_teams=32, recent_ppg=22.0, recent_games=4, form_projection=180),
        "hurt": PlayerOutlook(provider_projection=120, adjusted_projection=110, games_remaining=8, sos_rank=30,
                              sos_teams=32, recent_ppg=9.0, recent_games=4, form_projection=90),
    }
    svc = ValuationService(InMemoryPlayerRepository([hot, hurt, spare]), outlooks)
    h, i = svc.value(hot, league), svc.value(hurt, league)
    assert "Easiest remaining schedule for WRs" in h.strengths
    assert any("points above projections" in s for s in h.strengths)
    assert "Projections agree (ESPN 152 vs Sleeper 148)" in h.strengths
    assert "3rd hardest remaining schedule for WRs" in i.concerns
    assert any("points below projections" in c for c in i.concerns)
    assert "Listed Q: trade value cut to 90%" in i.concerns
    assert "Projections disagree (ESPN 140 vs Sleeper 100), so the outlook is less certain" in i.concerns


def test_kickers_and_defenses_are_flagged_as_streamable():
    league = LeagueSettings(num_teams=1, roster_slots={"K": 1, "DST": 1})
    players = make(Position.K, 90, 80) + make(Position.DST, 100, 70)
    svc = service(players)
    assert "Kickers swing week to week and are easy to stream" in svc.value(players[0], league).concerns
    assert "Defenses swing week to week and are easy to stream" in svc.value(players[2], league).concerns
