"""Placeholder player pool. Fictional names and numbers — replace with a real feed."""

from app.models.player import Player, Position

SAMPLE_PLAYERS: list[Player] = [
    Player(id="qb1", name="Sample QB One", position=Position.QB, team="KC", bye_week=6, ros_projection=210.0),
    Player(id="qb2", name="Sample QB Two", position=Position.QB, team="BUF", bye_week=7, ros_projection=195.0),
    Player(id="rb1", name="Sample RB One", position=Position.RB, team="SF", bye_week=9, ros_projection=180.0),
    Player(id="rb2", name="Sample RB Two", position=Position.RB, team="ATL", bye_week=12, ros_projection=150.0),
    Player(id="rb3", name="Sample RB Three", position=Position.RB, team="DET", bye_week=5, ros_projection=120.0, injury_status="Q"),
    Player(id="wr1", name="Sample WR One", position=Position.WR, team="MIN", bye_week=6, ros_projection=175.0),
    Player(id="wr2", name="Sample WR Two", position=Position.WR, team="CIN", bye_week=10, ros_projection=160.0),
    Player(id="wr3", name="Sample WR Three", position=Position.WR, team="MIA", bye_week=11, ros_projection=125.0),
    Player(id="te1", name="Sample TE One", position=Position.TE, team="BAL", bye_week=14, ros_projection=110.0),
    Player(id="te2", name="Sample TE Two", position=Position.TE, team="LV", bye_week=10, ros_projection=85.0),
    Player(id="k1", name="Sample Kicker", position=Position.K, team="DAL", bye_week=7, ros_projection=75.0),
    Player(id="dst1", name="Sample Defense", position=Position.DST, team="PIT", bye_week=9, ros_projection=80.0),
]
