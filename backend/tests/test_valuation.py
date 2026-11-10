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
