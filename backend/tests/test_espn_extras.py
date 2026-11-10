from app.data import db
from app.data.sources import espn
from app.data.sources.espn import ProTeam


def test_parse_site_teams_applies_aliases():
    data = {"sports": [{"leagues": [{"teams": [
        {"team": {"id": "28", "abbreviation": "WSH", "location": "Washington", "name": "Commanders",
                  "displayName": "Washington Commanders", "color": "5a1414",
                  "logos": [{"href": "https://a.espncdn.com/wsh.png"}]}},
        {"team": {"id": "8", "abbreviation": "DET", "displayName": "Detroit Lions"}},
    ]}]}]}
    rows = espn.parse_site_teams(data)
    assert rows[0]["abbrev"] == "WAS"
    assert rows[0]["logo_url"] == "https://a.espncdn.com/wsh.png"
    assert rows[1]["logo_url"] is None


def test_parse_market(tmp_path):
    players = [
        {"id": 4429795, "fullName": "Jahmyr Gibbs", "defaultPositionId": 2, "proTeamId": 8,
         "ownership": {"percentOwned": 99.9, "percentStarted": 98.6, "averageDraftPosition": 1.8,
                       "auctionValueAverage": 72.3, "percentChange": 0.0},
         "draftRanksByRankType": {"STANDARD": {"rank": 1}, "PPR": {"rank": 2}}},
        {"id": 1, "fullName": "A Punter", "defaultPositionId": 7, "proTeamId": 8},
        {"id": 2, "fullName": "No Data", "defaultPositionId": 3, "proTeamId": 99},
    ]
    rows = espn.parse_market(players, {8: ProTeam("DET", 8)}, "2026-11-09")
    assert [r["espn_id"] for r in rows] == [4429795, 2]
    gibbs = rows[0]
    assert (gibbs["team"], gibbs["adp"], gibbs["rank_ppr"]) == ("DET", 1.8, 2)
    assert rows[1]["team"] is None and rows[1]["percent_owned"] is None
    assert db.upsert(db.connect(tmp_path / "t.db"), "espn_player_market", rows) == 2
