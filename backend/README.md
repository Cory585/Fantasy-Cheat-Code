# Backend

## Player data

The API reads players from a local cache (`backend/data/players.json`, gitignored).
Build or refresh it with the sync job, from `backend/`:

```
python -m app.data.sync                         # current week -> week 17, PPR
python -m app.data.sync --scoring half_ppr      # standard | half_ppr | ppr
python -m app.data.sync --start-week 10 --end-week 17
python -m app.data.sync --no-espn               # Sleeper projections only
```

Restart the server after syncing. If no cache exists, the API falls back to the
fictional sample players in `app/data/sample_players.py`.

### Sources

| Source | Used for | Notes |
|---|---|---|
| Sleeper `/v1/players/nfl` | Player pool, teams, injury status | Free, no auth. Sleeper asks for at most one pull per day. |
| Sleeper `/projections/nfl/{season}/{week}` | Weekly projections | Undocumented endpoint; may change. |
| ESPN `leaguedefaults/{1,3}?view=kona_player_info` | Weekly projections | Undocumented, no auth. `1` = standard, `3` = PPR; half-PPR is derived from PPR minus 0.5 x receptions. |
| ESPN `seasons/{season}?view=proTeamSchedules_wl` | Team abbreviations, bye weeks | |

Each player's `ros_projection` is the mean of the sources that project them
(`projection_sources` shows each one). Players are joined by Sleeper's `espn_id`
where present, otherwise by normalized name + position (+ team). D/ST is joined by team.

### Rate limiting

Every outbound request goes through `app/data/sources/http.py`:

- Requests to the same host are spaced at least `REQUEST_INTERVAL_SECONDS` apart (default `1.0`).
- 429 and 5xx responses are retried with exponential backoff, honoring `Retry-After`.

A full sync is about 13 requests and takes ~15 seconds. API requests never call
Sleeper or ESPN directly; they only read the cache.

### Settings (environment variables)

| Variable | Default |
|---|---|
| `PLAYER_CACHE_PATH` | `backend/data/players.json` |
| `REQUEST_INTERVAL_SECONDS` | `1.0` |
