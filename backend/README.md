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
python -m app.data.collect --seasons 2022-2026               # history (used to train the defense model)
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
| `team_game_offense` | nflverse play-by-play | Per offense per game: neutral pass rate over expected (coaching tendency), plays, dropbacks, sacks taken, turnovers, EPA per dropback |
| `game_weather` | ESPN venues + [Open-Meteo](https://open-meteo.com) | Forecast temperature, wind, and precipitation for the 3 hours from kickoff, for unplayed games (Open-Meteo data is CC BY 4.0, free for non-commercial use) |
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
