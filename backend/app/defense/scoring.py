"""Standard fantasy scoring for team defense / special teams (DST).

These are Sleeper's and Yahoo's default points-allowed tiers. ESPN's default tiers are on a
smaller scale, so this isn't ESPN scoring.

Caveat: these points are the DST model's target and the "actual" score of a played game, but
the synced DST consensus projection averages Sleeper's and ESPN's projections, each in its own
provider's scoring. A defense's projection and its actual points can therefore be on slightly
different scales.
"""


def points_allowed_score(points_allowed: float) -> float:
    """Points-allowed tiers (Sleeper and Yahoo default): 0 allowed = 10, 1-6 = 7, 7-13 = 4,
    14-20 = 1, 21-27 = 0, 28-34 = -1, 35+ = -4."""
    if points_allowed <= 0:
        return 10
    if points_allowed <= 6:
        return 7
    if points_allowed <= 13:
        return 4
    if points_allowed <= 20:
        return 1
    if points_allowed <= 27:
        return 0
    if points_allowed <= 34:
        return -1
    return -4


def dst_points(stats: dict, points_allowed: float, sacks: float | None = None) -> float:
    """Fantasy points for one defense in one game.

    `stats` is the defense's nflverse team stat line. Sack 1, interception 2, fumble
    recovery 2, defensive or return TD 6, safety 2, blocked punt/FG 2, plus points-allowed tiers.

    `sacks` is the team's sack count from play-by-play, when known. The stat line adds up
    sacks credited to players, so it misses team sacks no player was credited with (the 2022
    Eagles' line has 69 of their official 70, the 2023 Ravens' 58 of 60). Fantasy scoring
    counts every sack.
    """
    def g(key: str) -> float:
        value = stats.get(key) or 0
        return float(value) if isinstance(value, (int, float)) else 0.0

    return (
        (g("def_sacks") if sacks is None else float(sacks))
        + 2 * g("def_interceptions")
        + 2 * g("fumble_recovery_opp")
        + 6 * (g("def_tds") + g("special_teams_tds"))
        + 2 * g("def_safeties")
        + 2 * (g("def_punt_blocks") + g("def_fg_blocks"))
        + points_allowed_score(points_allowed)
    )
