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
| `DST_MODEL_PATH` | `backend/data/defense_model.json` |
| `KICKER_MODEL_PATH` | `backend/data/kicker_model.json` |
| `MODEL_WEIGHT` | `0.5` (share of a K/DST weekly projection from its matchup model) |

## Player and team database

`python -m app.data.collect` gathers stats, schedules, injuries, and market data into a
SQLite database at `backend/data/fantasy.db` (gitignored, about 50 MB for one season plus
the full schedule history). A full run is 9 requests and takes about 10 seconds.

```
python -m app.data.collect                                   # everything, current season
python -m app.data.collect --seasons 2022-2026               # history (used to train the K and DST models)
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

## Start/sit

`GET /start-sit` (and the page at `/start-sit/`) compares 2 to 8 players competing for one
lineup spot in one week and says who to start:

| Parameter | Default | Notes |
|---|---|---|
| `player` | required | Repeat once per player: `?player=7564&player=6794` |
| `week` | current week | Must be within the synced weeks. The current week is the first with games still to play (also in `/meta` as `current_week`). |
| `slot` | narrowest that fits | `QB`, `RB`, `WR`, `TE`, `FLEX` (RB/WR/TE), `SUPERFLEX` (QB/RB/WR/TE), `K`, `DST`. Every player must be eligible. |
| `starts` | 1 | How many of the players you can start (fewer than the number compared) |

How each player's week is scored (`app/services/start_sit.py`):

1. **Projection.** The mean of Sleeper's and ESPN's projections for that week. The sync job
   keeps these per week in `weekly_projections`; caches built before that, and sample data,
   use rest-of-season points per remaining game instead.
2. **Recent form against this opponent** (needs `app.data.collect`). Points per game over the
   last 4 games (within 6 weeks) times how much this opponent allows to the position, capped at
   ±25% of the projection and blended in at `FORM_WEIGHT`, as in trade values. Kickers and
   defenses use their [matchup models](#kicker-and-defense-models) instead when they're trained:
   the model's prediction, capped at ±50% of the projection, blended in at `MODEL_WEIGHT`.
3. **Injury risk.** Expected points are scaled by the chance of playing: Questionable 85%,
   Doubtful 30%, Out and IR 0%. Floor and ceiling describe the week if the player plays.
4. **Range.** Floor and ceiling are roughly the 10th and 90th percentile: the player's own
   week-to-week spread this season, shrunk toward a typical spread for the position when there
   are few games.
5. **Confidence.** The week is simulated 4,000 times (seeded, so results are repeatable).
   `recommendation.probability` is how often the last player in the lineup outscores the best
   one left out: 70% and up is `confident`, 58% and up is `lean`, anything closer is `toss_up`.
   Each player's `start_probability` is how often they were among the top `starts` scorers.

Each player also gets `reasons` (projection, matchup rank, Vegas implied team total, form,
matchup model, injury, bye), each marked `plus`, `minus`, or `neutral`. Byes are found from the
collected schedule (or the player's `bye_week` without one); games already final show the actual
score (for defenses, scored from the team's stat line).
Errors: `422 invalid_start_sit` (players can't share the spot, duplicates, too many starts),
`422 invalid_week`, `404 player_not_found`.

### Kicker and defense models

Kicker and DST scoring depends on the game more than the player, so each has a matchup model:
a ridge regression on pre-game features (only what was known before kickoff), trained on
every game since 2023 (`--seasons 2022-2026`; 2022 is the prior for 2023). Train them after
collecting, and again whenever you want them to learn from new weeks:

```
python -m app.defense.train      # -> data/defense_model.json
python -m app.kicker.train       # -> data/kicker_model.json
```

Each feature is blended with last season's value early in the year. Every feature has an
expected direction (more wind hurts kickers, a QB who takes sacks helps defenses); when
correlated features push one the wrong way, it's dropped and the rest refit, so each
explanation in `reasons` points the sensible way. The API reloads the models when the files
change. Without them, kickers and defenses use the plain matchup factor above.

| Model | Features (`app/defense/features.py`, `app/kicker/features.py`) |
|---|---|
| DST | Opponent Vegas total and expected points-allowed score; opponent sack, turnover, and pass rates, pace, and efficiency; the expected opposing QB's passer rating, sack rate, and interception rate (with injuries); opponent receiver injuries; the defense's sacks, sack rate, QB hits, passes defended, takeaways, and pass defense EPA; expected sacks and takeaways (both sides of the matchup combined); defenders out; spread; rest; home; wind; cold |
| Kicker | Team Vegas total and scoring; FG and PAT attempts per game; how often drives stall into FGs; pace and passing efficiency; FG accuracy (shrunk toward 85%); share of 50+ yard attempts; kicker points and FG tries the opponent allows; expected kicker points; dome, wind, cold, altitude, home |

Each training run prints out-of-sample backtests: fit on earlier seasons, score on the next.
Mean absolute error per game (lower is better) and average weekly rank correlation (how well it
orders teams, higher is better), as of week 9 of 2026:

| Test season | DST model | DST, Vegas only | Kicker model | Kicker, before (PPG × matchup) | Kicker, Vegas only |
|---|---|---|---|---|---|
| 2024 | 4.00 / 0.35 | 4.04 / 0.33 | 3.78 / 0.23 | 4.08 / 0.11 | 3.82 / 0.20 |
| 2025 | 4.07 / 0.33 | 4.12 / 0.34 | 3.75 / 0.20 | 3.91 / 0.17 | 3.80 / 0.11 |
| 2026 (weeks 1-9) | 3.78 / 0.31 | 3.82 / 0.30 | 3.79 / 0.08 | 4.04 / 0.11 | 3.79 / 0.04 |

Both are mostly noise week to week (R² around 0.1 for DST and under 0.05 for kickers), so the
gains are modest. The newer DST features (QB-specific rates, pass rush, coverage, spread,
rest, and the matchup combinations) changed DST error by less than 0.02 points per game. The
kicker model cuts error by 4-7% against the points-per-game estimate start/sit used before.
`MODEL_WEIGHT` isn't tuned: provider projections for past weeks aren't stored, so the blend
with them can't be backtested yet.

### Your team and lineup

The **My team** page (`/team/`) saves a roster (up to 40 players) and your league's lineup spots
in the browser's `localStorage` (key `fcc.team.v1`). There are no accounts: the team stays on that
device, and nothing is stored on the server. The start/sit page's **My lineup** mode sends the
roster and spots to `POST /start-sit/lineup`:

```json
{"roster_ids": ["4984", "6794", "9221"], "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}, "week": 11}
```

`slots` takes the same spots as `/start-sit` (0 to 5 of each). Spots are filled narrowest first
(position spots, then FLEX, then SUPERFLEX), each with the player left who has the most expected
points and fits; because each wider spot accepts everything a narrower one does, this gives the
best total (`tests/test_properties.py` checks it against brute force). Each spot names the best
benched `alternative` and how often the starter outscores them (`probability`, `confidence`);
`close_calls` lists the closest spots. Roster ids no longer in the player pool are skipped and
returned in `missing_ids`, so a saved team keeps working. Errors: `422 invalid_lineup` (no spots),
`422 invalid_week`, `404 player_not_found` (none of the roster is in the pool).

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
| 422 | `invalid_request`, `invalid_trade`, `invalid_draft_slot`, `invalid_start_sit`, `invalid_lineup`, `invalid_week` |
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
clients are keyed on `X-Forwarded-For`. Proxies append the address they saw, so the client is
taken from the right: set `TRUSTED_PROXY_HOPS` to the number of proxies in front of the app
(default 1). Anything further left was sent by the client and is ignored. Limits are per
process, so several workers multiply them.

**Headers.** Every response sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
a referrer policy, a permissions policy, and (except on `/docs` and `/redoc`) a Content Security
Policy: scripts only from this app (no inline scripts), images only from here and
`a.espncdn.com`, fonts from Google Fonts, and API calls only to this app.

## Player profiles

`GET /players/{id}/profile` (and the page at `/player/?id=<id>`, linked from player names)
returns a player's bio, NFL career stats, college stats, and combine results:

| Part | Source | How it's fetched |
|---|---|---|
| Bio (height, weight, age, college, draft), headshot | ESPN athlete API | On demand, cached |
| NFL career stats by season | ESPN `nfl/athletes/{id}/stats` | On demand, cached |
| College stats by season | ESPN `college-football/athletes/{id}/stats` (ESPN reuses the NFL id) | On demand, cached |
| Combine results and percentiles | nflverse `combine.csv`, joined on `pfr_id` | `python -m app.data.collect --datasets combine` |
| Fallback bio (height, weight, draft) | nflverse `players` | Already collected |

Combine percentiles compare a result with every combine participant at the same position
since 2000 (for the 40, 3-cone, and shuttle, lower is better).

**Keeping ESPN safe.** A profile fetch is three ESPN requests, so they're protected at every step:

- Only players in the app's player pool can be fetched; unknown ids are a 404 before any request.
- Results are cached in `backend/data/profiles.db` for `PROFILE_TTL_HOURS` (default 24), and
  players ESPN doesn't know are cached for a week. The cache is a separate file from
  `fantasy.db`, so writes don't make the API reload player data.
- Concurrent requests for the same player share one fetch, and at most 2 fetches run at once.
- Requests use the shared rate-limited client (one request per second per host), an 8-second
  timeout, one retry, and a 2 MB response cap.
- After a failure, that player isn't retried for 10 minutes.
- The `/profile` endpoint has its own strict incoming rate limit (see above).

ESPN responses are treated as untrusted: only known fields are kept, each is type-checked
and length-capped, and headshots are only accepted from `a.espncdn.com`.

**When ESPN is unavailable**, the profile still returns 200 with what's stored locally, and
`espn` says what happened:

| `espn.status` | Meaning |
|---|---|
| `fresh` / `cached` | ESPN data included (just fetched / from the cache) |
| `stale` | ESPN failed; an older cached copy is included |
| `unavailable` | No ESPN data. `code` is `upstream_busy` or `upstream_error`; `retry_after` is in seconds |
| `not_found` | ESPN has no athlete with this id (`code: espn_player_not_found`) |
| `not_applicable` | Team defenses and players without an ESPN id |

## Testing

```
pip install -r requirements-dev.txt
pytest                      # everything, about 30 seconds
pytest -m "not e2e"         # skip the browser tests
pytest -m e2e               # only the browser tests
pytest --cov=app            # with coverage
```

| Kind | Files | What it checks |
|---|---|---|
| Unit and API | `tests/test_*.py` | Each module and endpoint, including error codes and security headers |
| Property-based | `tests/test_properties.py` | Invariants for any input (Hypothesis): every draft pick has exactly one owner, plans stay consistent, the rate limiter never exceeds its budget, ESPN parsing survives arbitrary JSON |
| Concurrency stress | `tests/test_stress.py` | 40 threads × 25 profile requests: the concurrency cap holds, each player is fetched once, nothing hangs; concurrent cache writers; rate-limiter accuracy under contention |
| Fuzzing | `tests/test_api_fuzz.py` | Random query strings, paths, bodies, methods, and Accept headers: never a 500, errors always `{detail, code}`, security headers always present |
| End-to-end | `tests/e2e/` | The real pages in a headless browser (Playwright, using an installed Edge or Chrome): controls, keyboard focus, stale and failed loads, countdowns, text-only rendering of player data, no CSP violations or console errors |

The browser tests are hermetic: a local server with fake player and ESPN data, and every
request to another host is answered locally. They're skipped if Playwright or a browser isn't
installed. No test contacts ESPN, Sleeper, or nflverse. If a test hangs for more than two
minutes, pytest prints every thread's stack (`faulthandler_timeout` in `pytest.ini`).
