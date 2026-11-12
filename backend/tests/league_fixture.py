"""A small synthetic league in a real database, for the DST and kicker model tests.

Four teams play a round robin each week for two seasons. Scores, sacks, and kicks follow
simple rules so tests can reason about them: BUF is the strong team, NYJ the weak one.
"""

import json
from datetime import date, timedelta

from app.data import db

TEAMS = ["BUF", "MIA", "NE", "NYJ"]
STRENGTH = {"BUF": 3, "MIA": 2, "NE": 1, "NYJ": 0}
# Week -> pairs (away, home). Every team plays every week.
PAIRS = [[("MIA", "BUF"), ("NYJ", "NE")], [("BUF", "NE"), ("MIA", "NYJ")], [("NE", "MIA"), ("NYJ", "BUF")]]


def build_league(path, seasons=(2024, 2025), weeks=6, unplayed_from: int | None = None, roof="outdoors"):
    """Create the database. Games in `unplayed_from` and later (last season only) have no score."""
    conn = db.connect(path)
    games, team_stats, offense, players = [], [], [], []
    for season in seasons:
        start = date(season, 9, 7)
        for week in range(1, weeks + 1):
            for away, home in PAIRS[(week - 1) % len(PAIRS)]:
                played = not (unplayed_from and season == seasons[-1] and week >= unplayed_from)
                scores = {team: 14 + 4 * STRENGTH[team] for team in (away, home)}
                game_id = f"{season}_{week:02d}_{away}_{home}"
                spread = float(STRENGTH[home] - STRENGTH[away]) * 3  # home margin
                games.append({
                    "game_id": game_id, "season": season, "game_type": "REG", "week": week,
                    "gameday": (start + timedelta(days=7 * (week - 1))).isoformat(),
                    "away_team": away, "home_team": home,
                    "away_score": scores[away] if played else None, "home_score": scores[home] if played else None,
                    "spread_line": spread, "total_line": float(scores[away] + scores[home]),
                    "roof": roof, "temp": 60.0, "wind": 5.0, "espn_id": None,
                })
                if not played:
                    continue
                for team, opp in ((away, home), (home, away)):
                    s = STRENGTH[team]
                    fg = 1 + s  # stronger offenses kick more
                    team_stats.append({
                        "team": team, "season": season, "week": week, "season_type": "REG", "opponent": opp,
                        "stats": json.dumps({
                            "def_sacks": 1 + s, "def_interceptions": 1, "fumble_recovery_opp": 0,
                            "def_tds": 0, "special_teams_tds": 0, "def_safeties": 0, "def_qb_hits": 4 + s,
                            "def_pass_defended": 3 + s, "fg_made_30_39": fg, "fg_made_40_49": 1,
                            "fg_att": fg + 2, "fg_missed": 1, "pat_made": 1 + s // 2, "pat_att": 1 + s // 2,
                            "passing_tds": s // 2, "rushing_tds": 1,
                        }),
                    })
                    offense.append({
                        "game_id": game_id, "team": team, "season": season, "week": week, "season_type": "REG",
                        "opponent": opp, "plays": 60 + s, "dropbacks": 35, "sacks": 4 - s, "interceptions": 1,
                        "fumbles_lost": 0, "dropback_epa": 2.0 * s, "neutral_plays": 30, "neutral_dropbacks": 17,
                        "pass_oe_sum": 0.0, "pass_oe_n": 30,
                    })
                    players.append({
                        "gsis_id": f"QB-{team}", "season": season, "week": week, "season_type": "REG",
                        "name": f"{team} QB", "position": "QB", "team": team, "opponent": opp,
                        "fantasy_points": 15.0, "fantasy_points_ppr": 15.0,
                        "stats": json.dumps({"attempts": 30, "completions": 20, "passing_yards": 220,
                                             "passing_tds": 1, "passing_interceptions": 1,
                                             "sacks_suffered": 4 - s}),
                    })
                    players.append({
                        "gsis_id": f"K-{team}", "season": season, "week": week, "season_type": "REG",
                        "name": f"{team} K", "position": "K", "team": team, "opponent": opp,
                        "fantasy_points": 0.0, "fantasy_points_ppr": 0.0,
                        "stats": json.dumps({"fg_made_30_39": fg, "fg_made_40_49": 1, "fg_att": fg + 2,
                                             "fg_missed": 1, "pat_made": 1 + s // 2, "pat_att": 1 + s // 2}),
                    })
    for table, rows in (("games", games), ("team_week_stats", team_stats), ("team_game_offense", offense),
                        ("player_week_stats", players)):
        db.upsert(conn, table, rows)
    conn.commit()
    return conn
