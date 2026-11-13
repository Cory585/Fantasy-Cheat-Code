"""Standard fantasy scoring for team defense / special teams (DST)."""


def points_allowed_score(points_allowed: float) -> float:
    """Standard points-allowed tiers (ESPN/Yahoo default)."""
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


def dst_points(stats: dict, points_allowed: float) -> float:
    """Fantasy points for one defense in one game.

    `stats` is the defense's nflverse team stat line. Sack 1, interception 2, fumble
    recovery 2, defensive or return TD 6, safety 2, blocked punt/FG 2, plus points-allowed tiers.
    """
    def g(key: str) -> float:
        value = stats.get(key) or 0
        return float(value) if isinstance(value, (int, float)) else 0.0

    return (
        g("def_sacks")
        + 2 * g("def_interceptions")
        + 2 * g("fumble_recovery_opp")
        + 6 * (g("def_tds") + g("special_teams_tds"))
        + 2 * g("def_safeties")
        + 2 * (g("def_punt_blocks") + g("def_fg_blocks"))
        + points_allowed_score(points_allowed)
    )
