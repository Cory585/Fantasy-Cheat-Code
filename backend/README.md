# Backend

## Player data

The API reads players from a local cache (`backend/data/players.json`, gitignored).
Build or refresh it with the sync job, from `backend/`:

```
python -m app.data.sync                         # current week -> week 18, PPR
python -m app.data.sync --scoring half_ppr      # standard | half_ppr | ppr
python -m app.data.sync --start-week 10 --end-week 18
python -m app.data.sync --no-espn               # Sleeper projections only
```

The running API picks up a new cache without a restart. If no cache exists (or it can't be
read), the API falls back to the fictional sample players in `app/data/sample_players.py`, and
`/meta` reports `"source": "sample"`.

### Sources

| Source | Used for | Notes |
|---|---|---|
| Sleeper `/v1/players/nfl` | Player pool, teams, injury status | Free, no auth. Sleeper asks for at most one pull per day. |
| Sleeper `/projections/nfl/{season}/{week}` | Weekly projections | Undocumented endpoint; may change. |
| ESPN `leaguedefaults/{1,3}?view=kona_player_info` | Weekly projections | Undocumented, no auth. `1` = standard, `3` = PPR; half-PPR is derived from PPR minus 0.5 x receptions. |
| ESPN `seasons/{season}?view=proTeamSchedules_wl` | Team abbreviations, bye weeks | |

Each player's `ros_projection` is the mean of the sources that project them
(`projection_sources` shows each one). The API recomputes both from `weekly_projections` for
the current week on (skipping the current week once the player's team has played), so played
weeks stop counting as the season goes on, without a re-sync.

**The current week** (`app/services/season.py`) is shared by start/sit, tiers, tags, defenses,
and rest-of-season projections. With a collected schedule it's the first week with a game
still to play; without one it's the calendar week (weeks turn over on Tuesdays, counted from
Sleeper's season start date saved by the sync, or the Tuesday after Labor Day), kept within
the synced weeks. `/meta` reports it as `current_week`, along with `synced_week` (the week the
sync ran) and `stale` (true when that's before the current week: rest-of-season totals still
drop played weeks, but projections, injuries, and the player pool are from that week). Players are joined by Sleeper's `espn_id`
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
| `player_game_usage` | nflverse play-by-play | Per player per game: targets by field location (left/middle/right), air yards, deep (20+), behind-the-line, red-zone, end-zone, and third-down targets; red-zone and goal-line carries |
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
   ESPN's weekly projections over the remaining weeks, from the current week through week 18
   (games already played are skipped). Projections are for the scoring format the data was
   synced with; a trade request for another format is refused (`422 scoring_unavailable`).
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
   position who wouldn't start in a league of this size, counting FLEX slots (the best RB, WR,
   or TE left after the position spots) and then SUPERFLEX slots (the best QB, RB, WR, or TE
   left, usually a QB, so superflex leagues value QBs more), then discounted for injury status
   (Q ×0.9, D ×0.75, O ×0.6, IR ×0.2).

Each valuation in the `/trades/analyze` response includes the projection used and an `outlook`
with remaining opponents, schedule rank (1 = easiest at the position), and recent points per game.

### Why a side wins or loses, and each player's strengths

Every player in the response also gets a breakdown (`app/services/valuation.py`):
`position_rank` by projection, a `tier` for the league size (RB1 = top 12 RBs in a 12-team
league), whether they'd `starter` given the lineup slots, and plain-language `strengths` and
`concerns`: top-3 or starting status, points over replacement, remaining schedule, recent form
(adjusted for the schedule) against projections, the injury discount, whether Sleeper and ESPN
agree (within 5%) or disagree (15% or more), and that kickers and defenses are streamable.

Each side gets `reasons` (`app/services/trade_reasons.py`), most important first and marked
`plus`, `minus`, or `neutral`:

| Reason | Example |
|---|---|
| `value` | Receives 132 points of value for 108 sent (+24) |
| `best_player` | Gets the best player in the deal: Jahmyr Gibbs (RB1, 108 value) (3+ players in the trade) |
| `roster_spots` | Consolidates 2 players into 1, opening 1 roster spot for a waiver pickup |
| `starters` | Gets 1 starter back for 2 sent, in a league with these lineup slots |
| `positions` | Stronger at RB: +22 points of value / Thinner at WR: −46 points of value |
| `schedule` | Players coming in have easier remaining schedules (6% more points allowed by their opponents) |
| `form` | Gets a player whose recent form points above projections |
| `injury` | Takes on injury risk / Moves injury risk |
| `uncertainty` | Sleeper and ESPN disagree on a player coming in |

The trade page shows these as a "Why Team A wins / Team B loses" panel under the verdict and a
card per player below the value bars.

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
- `tier` on every target and board entry: the player's tier at their position, from the same
  tiering as `/tiers` (by projection, for a league of `teams`). Tiers come from the full pool,
  so they don't shift as players are drafted. `null` past the tiered depth.
- `picks[].tier_alerts`: positions whose best tier left at this pick is likely gone, or down to
  one player, by your next pick. Each has the `tier`, how many of its players are `left` at
  this pick, and how many are likely still there at your next (`left_at_next`), from the same
  ADP board as `position_mix`. Sorted most urgent first; none after your last pick. The page
  shows up to three per pick ("Last chance: all 3 Tier 1 RBs left are likely gone before 2.08").

It doesn't yet account for your roster so far.

For a draft in progress, pass the players already taken as repeated `drafted` ids, and
`picks_made` (picks in so far, which can be more than the drafted ids found, since a league can
draft someone outside the player pool). Drafted players come off the board, your past picks are
left out of `picks`, and `board` covers only the picks still to come (index 0 is pick
`picks_made + 1`).

### Live ESPN drafts

The draft page can follow an ESPN league's draft as it happens (`app/api/live_draft.py`):

| Endpoint | What it does |
|---|---|
| `POST /draft/live` | Body `{"league_id": "123456"}`, plus `espn_s2` and `swid` for a private league, and optionally `season` (default: this year). Reads the draft once, so a wrong id or cookie fails right away, then returns a `session_id` and the draft. |
| `GET /draft/live/{session_id}` | The draft now: status (`pre_draft`, `in_progress`, `complete`), teams in draft order, rounds, and every pick so far matched to the player pool by ESPN id. |
| `GET /draft/live/{session_id}/events` | Server-Sent Events: a `draft` event with the same JSON now and whenever a pick comes in, until the draft is complete. `problem` events (`{code, detail}`) report ESPN errors; the stream ends on `espn_private_league`, `espn_league_not_found`, or `live_draft_not_found`. |
| `DELETE /draft/live/{session_id}` | Ends the session and forgets the cookies. |

ESPN has no official API; this reads the league endpoint the ESPN draft room uses (views
`mDraftDetail`, `mSettings`, `mTeams`), so it can break if ESPN changes it. How it's kept safe
and light:

- **Cookies.** `espn_s2` and `SWID` are checked against strict patterns (so they can't inject
  headers), sent only to ESPN, held in memory only, and never logged or echoed back. They go
  when the session is deleted or unused for `LIVE_DRAFT_SESSION_HOURS` (default 6). The page
  clears them from its form once sent and keeps only the session id, in `sessionStorage`.
- **The session id** (32 random URL-safe characters) is the only key to a session, so treat it
  like a password for that draft.
- **Polling.** ESPN is read at most once per `LIVE_DRAFT_POLL_SECONDS` (default 4) per session,
  however many streams or tabs follow it. A stream runs for up to `LIVE_DRAFT_STREAM_HOURS`
  (default 1) before the browser reconnects, and stops as soon as its session ends.
- **Limits.** At most `LIVE_DRAFT_MAX_SESSIONS` (default 80) sessions at once, and connecting
  has its own rate limit bucket (below). All sessions share one ESPN rate,
  `LIVE_DRAFT_ESPN_REQUESTS_PER_SECOND` (default 20), so keep the session cap at or below that
  rate times the poll interval; a failed read is reused until the interval passes too.

The plan assumes a snake draft; for other ESPN draft types the picks still come through, and the
page says the plan may not match.

## Tier lists

`GET /tiers` (and the page at `/tiers/`) groups the players at a position into tiers: players
in the same tier are roughly interchangeable, and a new tier starts where projected points drop
sharply.

| Parameter | Default | Notes |
|---|---|---|
| `position` | required | `QB`, `RB`, `WR`, `TE`, `K`, `DST`, or `FLEX` (RB, WR, and TE ranked together) |
| `horizon` | `ros` | `ros` (rest of season) or `week` |
| `week` | current week | Only with `horizon=week`; must be within the synced weeks |
| `teams` | 12 | 4 to 20. Sets how deep the list goes, and flex replacement levels |
| `depth` | scaled to `teams` | 1 to 300 players. Default per team: QB 2.5, RB 5, WR 6, TE 2.5, K 1.5, DST 1.5, FLEX 10 |

Single positions are tiered by projected points (the schedule- and form-adjusted projection
for the rest of the season, or the week's consensus projection). FLEX is tiered by points over
replacement, so RBs, WRs, and TEs compare fairly; replacement levels come from filling every
lineup in a league of `teams` (the same as trade values). Players with no projection are left
out, so the week view is empty if the sync has no weekly projections for that week.

How breaks are placed (`app/services/tiers.py`): players are sorted by value and split into
contiguous tiers by optimal 1-D clustering, a dynamic program that minimizes the spread within
tiers (sum of squared distances to each tier's mean) plus a fixed cost per tier. That cost is
scaled to the average gap between neighboring players, so it works the same for season totals
and single weeks: a smooth run grows to a tier of about a sixth of the list (4 to 8 players)
before it splits, and one drop several times the average gap starts a tier on its own.

Each tier in the response has its `players` (with `rank`, `points`, and the tiered `value`),
its `high` and `low` values, and `drop_to_next`, the gap to the next tier's best player.

## Start/sit

`GET /start-sit` (and the page at `/start-sit/`) compares 2 to 8 players competing for one
lineup spot in one week and says who to start:

| Parameter | Default | Notes |
|---|---|---|
| `player` | required | Repeat once per player: `?player=7564&player=6794` |
| `week` | current week | Must be within the synced weeks. The current week is the first with games still to play, or the calendar week without a collected schedule (also in `/meta` as `current_week`). |
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
every collected season after the first (the first is only the prior). Train them after
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
| DST | Opponent Vegas total (estimated when there's no line yet) and expected points-allowed score; point spread; opponent sack rate, pass-heavy play-calling, and pace; the expected opposing QB's sack and interception rates (with injuries); the defense's takeaways, QB hits, and passes defended; expected takeaways (both sides of the matchup combined); defenders out; wind |
| Kicker | Team Vegas total; point spread (favorites kick more); FG accuracy (shrunk toward 85%) and PAT accuracy (toward 95%); share of 50+ yard attempts; kicker points the opponent allows; dome, wind, cold, altitude, home |

Each training run prints out-of-sample backtests: fit on earlier seasons, score on the next.
Mean absolute error per game (lower is better) and average weekly rank correlation (how well it
orders teams, higher is better), as of week 9 of 2026:

| Test season | DST model | DST, Vegas only | Kicker model | Kicker, before (PPG × matchup) | Kicker, Vegas only |
|---|---|---|---|---|---|
| 2020 | 4.08 / 0.32 | 4.03 / 0.32 | | | |
| 2021 | 4.36 / 0.26 | 4.36 / 0.27 | | | |
| 2022 | 3.87 / 0.21 | 3.88 / 0.20 | | | |
| 2023 | 4.27 / 0.28 | 4.28 / 0.27 | | | |
| 2024 | 3.99 / 0.35 | 4.07 / 0.33 | 3.77 / 0.23 | 4.08 / 0.11 | 3.82 / 0.20 |
| 2025 | 4.10 / 0.33 | 4.18 / 0.34 | 3.73 / 0.22 | 3.91 / 0.17 | 3.80 / 0.11 |
| 2026 (weeks 1-9) | 3.81 / 0.32 | 3.88 / 0.30 | 3.79 / 0.07 | 4.04 / 0.11 | 3.79 / 0.04 |

DST numbers are from 2018-2026 collected (`--seasons 2018-2026`), so the model trains on up to
seven seasons; the kicker columns are from 2022-2026. With only 2019 to learn from, the 2020
model (the no-fans season) was slightly worse than Vegas alone and ran about 0.6 points high;
from 2021 it matches or beats Vegas. `tests/realdata/test_real_model.py` holds it to that.

Both are mostly noise week to week (R² around 0.1 for DST and under 0.05 for kickers), so the
gains are modest. The newer DST features (QB-specific rates, pass rush, coverage, spread,
rest, and the matchup combinations) changed DST error by less than 0.02 points per game. The
kicker model cuts error by 4-7% against the points-per-game estimate start/sit used before.

The kicker model is deliberately small. Team scoring, pace, passing efficiency, attempts per
game, how often drives stall into field goals, the opponent's points and FG tries allowed,
and an expected-points estimate built from TD and FG drive rates were all tried. None added
anything out of sample once the Vegas line was in, so they were dropped. The spread matters
beyond the implied total: favorites kick more, because they take the points late and run out
the clock. Kicking has also changed: 50+ yard attempts went from 14% of tries in 2019 to 26%
in 2026, and league kicker scoring rose from about 7.4 to 8.5 points per game. So the kicker
model weights recent seasons more (a 2-season half-life), and with 4+ seasons its intercept
comes from the league trend rather than the lagging training average. That makes projections
unbiased (their mean matches the season's). Because kicker scores are right-skewed, it costs
about 0.01 in MAE for a lower RMSE.

The kicker job trains on every season in the database after the first. Collecting older
seasons (`python -m app.data.collect --seasons 2017-2026`) gives it more history to learn the
trend from: backtested on 2019-2026, the model then averages R² 0.029 (0.021 before these
changes), and it orders kickers better than the Vegas-only baseline in 7 of 8 seasons (its
MAE is lower in 5).

The DST model is small for the same reason. Opponent scoring, turnover rate, and passing
efficiency, QB passer rating and availability, receiver injuries, the defense's own sacks,
sack rate, points allowed, fantasy points per game, and pass EPA, expected sacks, rest, home,
and cold added nothing once the Vegas line was in: the sign constraint dropped most of them
every season. Dropping them lowered MAE and raised rank correlation slightly in every
backtest season. Recency weighting, separate models for sacks, takeaways, and points
allowed, and QB priors from the previous season were tried too, and didn't help.

Lines only exist about a week ahead, so later weeks used to fall back to the opponent's raw
points per game and a pick'em spread. Now they use an estimated line: a fit of Vegas implied
points on the team's scoring and the opponent's points allowed, made from the loaded games
that have lines. With lines removed from the backtest seasons, the stand-in line's error
falls from 2.4 to 1.8 points and the model's weekly rank correlation rises from 0.235 to
0.246. Reasons built on an estimate say "projected" instead of "Vegas".

Two data fixes came out of checking against real history. DST points count every sack from
play-by-play: nflverse's team stat lines only add up sacks credited to players, so they miss
team sacks (the 2022 Eagles' line had 69 of their official 70). And relocated franchises use
their current code in every table: schedules and injury reports had the 2018-2019 Raiders as
OAK while the stats said LV, which dropped them from those seasons.

`MODEL_WEIGHT` isn't tuned: provider projections for past weeks aren't stored, so the blend
with them can't be backtested yet.

## Defense breakdown

`/defense/` (API: `GET /defenses` and `GET /defenses/{team}?season=`) ranks every defense's
position groups and units across the league, from `app/services/defense_breakdown.py`:

- **Position groups**: the line (DE, DT, NT), linebackers (including OLB edge rushers, as
  nflverse lists them), corners, and safeties. Each group's stats are summed over its players
  for the team they played for that week, per team game. A group is ranked on a weighted
  average of league z-scores of the stats that measure its job: sacks, QB hits, and tackles
  for loss up front; passes defended and interceptions in the secondary. Tackles barely count,
  since lots of tackles can just mean the offense kept the ball.
- **Units**: pass rush (sack rate, sacks, QB hits), coverage (EPA per dropback, passing yards,
  interception rate allowed), run defense (yards per carry, rushing yards and TDs allowed),
  and scoring (points allowed, DST points, takeaways), from what opposing offenses did.
- **Fantasy points allowed** per game to QB, RB, WR, and TE (PPR), and the **next three
  games** with the DST model's projection, its rank that week, and the reasons.

Breakdowns are computed once per season and rebuilt when the database changes.

### Leagues, your team, and lineup

A profile holds up to **10 leagues**, each with its own name, lineup spots (0 to 5 of each), and
roster (up to 40 players). There are no accounts: the browser makes a random profile key
(32 bytes, base64url, kept in `localStorage` as `fcc.profile.v1`) and sends it with every league
request as `X-Profile-Key`. The server stores only the key's SHA-256, in
`backend/data/leagues.db` (gitignored; `LEAGUE_DB_PATH`), and every query is scoped to it, so
one profile can't see or change another's leagues. The **Profile** page (`/profile/`) adds,
renames, and removes leagues, and shows the key so the same leagues can be opened on another
device. **My team** (`/team/`) and **Start/sit** (`/start-sit/`) each have a league picker
(or take `?league=<id>`). A team saved by the earlier single-team version (`fcc.team.v1`) is
moved into a league the first time the profile loads with none.

| Request | Does |
|---|---|
| `GET /leagues` | `{"leagues": [...], "max_leagues": 10}`, oldest first |
| `POST /leagues` | Adds one: `{"name": "Work league", "slots": {"SUPERFLEX": 1}, "roster": ["4984"]}` (missing spots get the defaults). `201` |
| `GET /leagues/{id}` | One league |
| `PUT /leagues/{id}` | Replaces its name, spots, and roster (same body as `POST`). With `If-Match: "<version>"`, only if nobody has saved it since |
| `DELETE /leagues/{id}` | Removes it. `204` |

Each league has a `version` that goes up by one with every save (also sent as the `ETag`
header). The pages send the version they last saw as `If-Match`; if the league was saved
somewhere else since (another tab or device), the save is refused with `409 league_conflict`
and the body's `league` holds the current copy, so nothing is silently overwritten. A `PUT`
without `If-Match` replaces the league as before.

Errors: `400 invalid_if_match` (an `If-Match` that isn't a version), `401 profile_key_required`
(missing or malformed key), `404 league_not_found` (no such league on this profile),
`409 league_limit` (already 10), `409 league_conflict` (saved elsewhere since the `If-Match`
version), `503 leagues_unavailable` (the store
couldn't be read or written), `503 leagues_full` (the server holds `MAX_STORED_LEAGUES`,
default 100,000, across all profiles). The 10-league check and the insert share a transaction,
so simultaneous requests can't slip past the cap. Making and removing leagues is rate limited
(see below).

The start/sit page's **My lineup** mode sends the chosen league's
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

## Player tags

`GET /tags` returns 35 tags for quick lookup, each with what earns it, how many players have it,
and every player's tags with the numbers behind them (`?player=<id>` for one player). The
**Player finder** page (`/finder/`) filters by them, and player profiles link to it.

| Category | Tags |
|---|---|
| Role | Slot receiver, outside receiver, deep threat, possession receiver, after-the-catch threat, screen and short-area target, alpha receiver, receiving tight end, workhorse back, committee back, pass-catching back, goal-line back, rushing QB, gunslinger, high-volume passer |
| Usage | High target share, air-yards leader, red-zone target, end-zone target, red-zone carries, third-down target, full-time snaps, part-time role, snaps rising |
| Trend | Hot streak, cold streak, boom or bust, steady producer, TD-dependent, trending adds (ESPN) |
| Status | Rookie, injury designation, on bye this week, easy or tough schedule ahead |

Role, usage, and trend tags use the current season's regular-season games (3+ games, and 15+
targets for receiving-style tags) from `player_week_stats`, `player_game_usage`, and
`snap_counts`; they're rebuilt when the data reloads. Thresholds are in each tag's
`description` and in `app/services/tags.py`.

**Slot vs outside.** No public data charts where a receiver lines up, so it's estimated from
where and how deep they're targeted: slot receivers are thrown to between the numbers and short
(a quarter or more of targets in the middle at a 10.5-yard average depth or less, 18%+ in the
middle at 8.5 yards or less, or any 6.5-yard average depth or less); outside receivers are
thrown to near the sidelines and deeper (20% or less in the middle at 10+ yards). On 2026 data
this puts Amon-Ra St. Brown, Khalil Shakir, Zay Flowers, and Keenan Allen in the slot and
DK Metcalf, George Pickens, Chris Olave, and Matthew Golden outside. A receiver who moves
around may get neither tag.

## Errors, limits, and security

**Errors.** Every error response is JSON shaped as `{"detail": "...", "code": "..."}`.
`detail` is a readable message (a list of field errors for `422 invalid_request`); `code` is
stable for clients to branch on:

| Status | Codes |
|---|---|
| 400 | `bad_request` (including unparseable bodies), `invalid_if_match` |
| 401 | `profile_key_required` |
| 403 | `espn_private_league` (ESPN refused: add or refresh the league's cookies) |
| 404 | `not_found`, `player_not_found`, `league_not_found`, `espn_league_not_found`, `live_draft_not_found` |
| 405 | `method_not_allowed` |
| 409 | `league_limit`, `league_conflict` |
| 413 | `payload_too_large` (bodies over 256 KB) |
| 422 | `invalid_request`, `invalid_trade`, `scoring_unavailable`, `invalid_draft_slot`, `invalid_drafted`, `invalid_start_sit`, `invalid_lineup`, `invalid_week` |
| 429 | `rate_limited` (with a `Retry-After` header, in seconds) |
| 500 | `internal_error` (details are logged on the server, never returned) |
| 502 / 503 / 504 | `upstream_error`, `espn_unavailable`, `service_unavailable`, `leagues_unavailable`, `leagues_full`, `live_draft_busy`, `upstream_timeout` |

Validation errors don't echo the submitted input back. Browsers that hit a missing page get
`frontend/404.html`; API clients (no `text/html` in `Accept`) get the JSON 404.

**Rate limits.** Per client IP, in memory (`app/api/ratelimit.py`):

| Bucket | Applies to | Burst | Sustained |
|---|---|---|---|
| `all` | every request | 300 | 5 per second |
| `upstream` | endpoints that call outside providers (`/players/{id}/profile`) | 10 | 1 every 5 seconds |
| `league-changes` | making or removing a league (`POST /leagues`, `DELETE /leagues/{id}`) | 12 | 4 a minute |
| `league-edits` | saving a league (`PUT /leagues/{id}`) | 60 | 1 per second |
| `live-draft-start` | connecting a live ESPN draft (`POST /draft/live`) | 6 | 3 a minute |

A rule can be limited to certain methods, so reading leagues only counts against `all`. The
league-change limits can be tuned with `LEAGUE_CHANGE_BURST` and `LEAGUE_CHANGES_PER_SECOND`.

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
pytest                      # everything, about a minute
pytest -m "not e2e"         # skip the browser tests
pytest -m e2e               # only the browser tests
pytest -m realdata          # only the checks against real NFL data
pytest --cov=app            # with coverage
```

| Kind | Files | What it checks |
|---|---|---|
| Unit and API | `tests/test_*.py` | Each module and endpoint, including error codes and security headers |
| Property-based | `tests/test_properties.py` | Invariants for any input (Hypothesis): every draft pick has exactly one owner, plans stay consistent, the rate limiter never exceeds its budget, ESPN parsing survives arbitrary JSON |
| Concurrency stress | `tests/test_stress.py` | 40 threads × 25 profile requests: the concurrency cap holds, each player is fetched once, nothing hangs; concurrent cache writers; rate-limiter accuracy under contention |
| Fuzzing | `tests/test_api_fuzz.py` | Random query strings, paths, bodies, methods, and Accept headers: never a 500, errors always `{detail, code}`, security headers always present |
| Real NFL data | `tests/realdata/` | The collected data, defense breakdown, and DST model against what actually happened, 2018-2025. History: the fewest points allowed each season, sack leaders with official totals (team sacks included), takeaway leaders, standout seasons (T.J. Watt's 22.5 sacks in 2021, Trevon Diggs' 11 interceptions, Sauce Gardner's 20 passes defended), the Dolphins' 70-20 win over Denver, 32 teams with the right game counts every season. Model: season-by-season backtests against baselines and Vegas, calibration, the weekly top five outscoring the bottom five, the easiest matchup projecting above the hardest in every real week, estimated lines against real ones, and no peeking at the game being predicted |
| End-to-end | `tests/e2e/` | The real pages in a headless browser (Playwright, using an installed Edge or Chrome): controls, keyboard focus, stale and failed loads, countdowns, text-only rendering of player data, no CSP violations or console errors |

The browser tests are hermetic: a local server with fake player and ESPN data, and every
request to another host is answered locally. They're skipped if Playwright or a browser isn't
installed. The real-data tests read `backend/data/fantasy.db` read-only and are skipped unless
2018-2026 history is collected (`python -m app.data.collect --seasons 2018-2026`). No test
contacts ESPN, Sleeper, or nflverse. If a test hangs for more than two
minutes, pytest prints every thread's stack (`faulthandler_timeout` in `pytest.ini`).
