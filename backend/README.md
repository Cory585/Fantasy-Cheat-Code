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
| `DATABASE_PATH` | `backend/data/fantasy.db` |
| `REQUEST_INTERVAL_SECONDS` | `1.0` |

## Player and team database

`python -m app.data.collect` gathers stats, schedules, injuries, and market data into a
SQLite database at `backend/data/fantasy.db` (gitignored, about 50 MB for one season plus
the full schedule history). A full run is 9 requests and takes about 10 seconds.

```
python -m app.data.collect                                   # everything, current season
python -m app.data.collect --season 2025                     # a past season
python -m app.data.collect --datasets player_week_stats,injuries
```

Re-running replaces rows with the same key, so it's safe to run daily. `espn_market` keeps
one snapshot per day, so trends in rostered % and ADP build up over time.

| Table | Source | What's in it |
|---|---|---|
| `teams` | ESPN sports API | Names, colors, logos |
| `players` | nflverse | Every player, with GSIS, ESPN, PFR, and PFF IDs, bio, and draft info |
| `games` | nflverse | Schedule and results since 1999, with Vegas spread and total, roof, weather |
| `player_week_stats` | nflverse | ~150 stats per player per week (passing, rushing, receiving, kicking, defense, target share, fantasy points) |
| `team_week_stats` | nflverse | Team offense, defense, and special teams per week |
| `snap_counts` | nflverse | Offense, defense, and special teams snaps and share |
| `injuries` | nflverse | Weekly injury report status and practice participation |
| `espn_player_market` | ESPN Fantasy | Rostered %, started %, ADP, auction value, standard and PPR draft rank |
| `collection_runs` | | When each dataset was last collected |

### Linking players across sources

The `players` table maps IDs between providers. Trade-analyzer players carry an `espn_id`,
which joins to `players.espn_id`, then to stats and injuries by `gsis_id` and to snap
counts by `pfr_id`. About 94% of synced players link this way today.

Stats beyond the promoted columns live in a JSON `stats` column:

```sql
SELECT name, week, fantasy_points_ppr, json_extract(stats, '$.target_share') AS target_share
FROM player_week_stats
WHERE season = 2026 AND position = 'WR'
ORDER BY fantasy_points_ppr DESC
LIMIT 10;
```

### Other ESPN data available (not collected yet)

ESPN's public sports API (`site.api.espn.com`, no auth) also serves team rosters with
depth info, per-player game logs, news, scoreboards, and standings. These are easy to add
as new datasets. Private ESPN league data (your league's rosters and settings) needs the
`espn_s2` and `SWID` cookies from a logged-in browser.

All sources here are public APIs or published data files. No scraping is needed.

## How trade values are calculated

1. **Projection.** Each player's rest-of-season projection starts as the mean of Sleeper's and
   ESPN's weekly projections over the remaining weeks (games already played are skipped).
2. **Schedule-adjusted recent form** (needs `app.data.collect`). Average points over the last
   4 games (within the last 6 weeks), times remaining games, times the player's strength of
   schedule. The final projection is 70% provider projection and 30% this estimate
   (`FORM_WEIGHT`). The form estimate is capped at ±25% of the provider projection, because it
   can't see role changes like a starter returning from injury. Players listed Out or on IR,
   and team defenses, use the provider projection only.
3. **Strength of schedule.** Each defense's fantasy points allowed per game to the position this
   season, relative to the league average and shrunk toward average for small samples, then
   averaged over the player's remaining opponents. Kickers use kicker points allowed (computed
   from field goals and extra points); defenses use opponents' points scored. Provider
   projections already account for opponents, so the schedule only adjusts the form estimate,
   so it isn't counted twice.
4. **Value over replacement.** Value is the projection minus the best player at the same
   position who wouldn't start in a league of this size, counting FLEX slots, then discounted
   for injury status (Q ×0.9, D ×0.75, O ×0.6, IR ×0.2).

Each valuation in the `/trades/analyze` response includes the projection used and an `outlook`
with remaining opponents, schedule rank (1 = easiest at the position), and recent points per game.

## Draft guide

`GET /draft/plan` (and the page at `/draft/`) lays out a snake draft for your league size,
draft slot, and number of rounds:

| Parameter | Default | Range |
|---|---|---|
| `teams` | 12 | 4 to 20 |
| `slot` | required | 1 to `teams` (your draft position; 1 = first pick) |
| `rounds` | 15 | 1 to 25 |
| `targets` | 5 | 1 to 20 players per pick |

Your pick in round *r* is `slot` in odd rounds and `teams + 1 - slot` in even rounds.

Players are ranked into one board: first by ESPN average draft position (the latest
`espn_player_market` snapshot, joined on `espn_id`), then players with no ADP by projection.
At each pick, every player ranked ahead of it is assumed to be gone, and the next `targets`
players are suggested. Without a collected database, the whole board is ranked by projection
(`adp_source` in the response says which).

The response also includes what the page's diagrams use:

- `board`: a mock draft for every pick in the draft (`teams x rounds`), assuming each team takes
  the top-ranked player left. The page colors it by position, so position runs stand out.
- `picks[].picks_until_next`: how many picks other teams make before your next one (0 at the turn,
  `null` after your last pick). The page draws these as arcs on the pick timeline.
- `picks[].position_mix`: the positions of the players other teams likely take before your next
  pick, assuming you take the top-ranked player. The page shows it as a stacked bar on each pick.

This is a skeleton. It doesn't yet account for your roster so far, tiers, or positional runs.

## Errors, limits, and security

**Errors.** Every error response is JSON shaped as `{"detail": "...", "code": "..."}`.
`detail` is a readable message (a list of field errors for `422 invalid_request`); `code` is
stable for clients to branch on:

| Status | Codes |
|---|---|
| 400 | `bad_request` (including unparseable bodies) |
| 404 | `not_found`, `player_not_found` |
| 405 | `method_not_allowed` |
| 413 | `payload_too_large` (bodies over 256 KB) |
| 422 | `invalid_request`, `invalid_trade`, `invalid_draft_slot` |
| 429 | `rate_limited` (with a `Retry-After` header, in seconds) |
| 500 | `internal_error` (details are logged on the server, never returned) |
| 502 / 503 / 504 | `upstream_error`, `service_unavailable`, `upstream_timeout` |

Validation errors don't echo the submitted input back. Browsers that hit a missing page get
`frontend/404.html`; API clients (no `text/html` in `Accept`) get the JSON 404.

**Rate limits.** Per client IP, in memory (`app/api/ratelimit.py`):

| Bucket | Applies to | Burst | Sustained |
|---|---|---|---|
| `all` | every request | 300 | 5 per second |
| `upstream` | endpoints that call outside providers (`.../profile`) | 10 | 1 every 5 seconds |

Set `RATE_LIMIT_ENABLED=0` to turn them off. Behind a reverse proxy, set `TRUST_PROXY=1` so
clients are keyed on `X-Forwarded-For` (only when your proxy sets that header). Limits are
per process, so several workers multiply them.

**Headers.** Every response sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
a referrer policy, a permissions policy, and (except on `/docs` and `/redoc`) a Content Security
Policy: scripts only from this app (no inline scripts), images only from here and
`a.espncdn.com`, fonts from Google Fonts, and API calls only to this app.
