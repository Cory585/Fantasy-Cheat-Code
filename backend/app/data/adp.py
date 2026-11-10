"""Average draft position from the collected ESPN market snapshots."""

import sqlite3


def load_adp(conn: sqlite3.Connection) -> dict[int, float]:
    """ESPN id -> ADP from the most recent snapshot. Players ESPN reports with no ADP
    (0 or missing) are left out. Returns {} if the market table hasn't been collected."""
    try:
        rows = conn.execute(
            """
            SELECT espn_id, adp FROM espn_player_market
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM espn_player_market)
              AND adp > 0
            """
        ).fetchall()
    except sqlite3.OperationalError:  # no such table
        return {}
    return {int(espn_id): float(adp) for espn_id, adp in rows}
