"""Weekly start/sit: who to start this week among players competing for the same lineup spot.

For each player and week:

1. **Projection.** The mean of Sleeper's and ESPN's projections for that week. Players with
   no weekly projections (sample data, old caches) use rest-of-season points per remaining game.
2. **Recent form vs this opponent** (needs `app.data.collect`). Average points over the last
   FORM_GAMES games times how much this week's opponent allows to the position, capped at
   +/-FORM_CAP of the provider projection and blended in at `form_weight` (as in trade values).
3. **Injury risk.** Expected points are scaled by the chance the player suits up
   (PLAY_PROBABILITY); floor and ceiling describe the week if the player plays.
4. **Range.** The spread of outcomes is the player's own week-to-week spread this season,
   shrunk toward a typical spread for the position (POSITION_CV) when there are few games.

Players are ranked by expected points, and a seeded simulation of the week (normal outcomes,
injuries as a coin flip) says how often the call is right, so close calls look close.
"""

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.player import Player, Position
from app.models.start_sit import (
    SLOT_POSITIONS,
    Confidence,
    Effect,
    GameStatus,
    LineupSlot,
    Matchup,
    PlayerWeek,
    Reason,
    Recommendation,
    StartSitAnalysis,
)
from app.services.matchups import MatchupData
from app.services.outlook import schedule_label

FORM_GAMES = 4
MIN_FORM_GAMES = 3
FORM_WINDOW_WEEKS = 6
FORM_CAP = 0.25
# Chance a player plays this week, by injury designation.
PLAY_PROBABILITY = {"Q": 0.85, "D": 0.3, "O": 0.0, "IR": 0.0}
# Typical week-to-week standard deviation as a share of projection, by position.
POSITION_CV = {
    Position.QB: 0.38, Position.RB: 0.5, Position.WR: 0.55,
    Position.TE: 0.6, Position.K: 0.45, Position.DST: 0.6,
}
# Games of a player's own history that count as much as the position default.
SPREAD_PRIOR_GAMES = 6
MIN_SPREAD_GAMES = 4
Z_10 = 1.2816  # 10th/90th percentile of a normal distribution
CONFIDENT_AT = 0.7
LEAN_AT = 0.58
SIMULATIONS = 4000


class StartSitError(ValueError):
    """The request can't be answered (wrong slot, duplicate players, bad week)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class StartSitAdvisor:
    matchups: MatchupData
    season: int | None
    weeks: tuple[int, int]  # first and last week with projections
    form_weight: float = 0.3
    simulations: int = SIMULATIONS

    # ---------- public ----------

    @property
    def current_week(self) -> int:
        """First week in range with a game still to be played (the first week without a schedule)."""
        first, last = self.weeks
        if self.matchups.available:
            for week in range(first, last + 1):
                games = [w[week] for w in self.matchups.schedule.values() if week in w]
                if any(not g.played for g in games):
                    return week
        return first

    def check_week(self, week: int | None) -> int:
        if week is None:
            return self.current_week
        first, last = self.weeks
        if not first <= week <= last:
            raise StartSitError("invalid_week", f"Week must be between {first} and {last}.")
        return week

    def evaluate(self, player: Player, week: int) -> PlayerWeek:
        """One player's outlook for one week (projection, range, matchup, and reasons)."""
        game = self.matchups.game(player.team, week)
        on_bye = game is None if self.matchups.has_schedule(player.team) else player.bye_week == week
        reasons: list[Reason] = []
        sources = player.weekly_projections.get(week, {})
        provider = player.week_projection(week)

        if on_bye:
            reasons.append(Reason(kind="bye", text=f"On bye in week {week}.", effect=Effect.MINUS))
            return PlayerWeek(player=player, week=week, status=GameStatus.BYE, projection=0, if_active=0,
                              floor=0, ceiling=0, play_probability=0, reasons=reasons)

        matchup = self._matchup(player, game, week) if game else None
        history = self.matchups.player_history(player.espn_id)

        if game and game.played:
            actual = history.get(week)
            text = (f"Game is final: scored {actual:.1f}." if actual is not None
                    else "Game is final; no score collected yet.")
            reasons.append(Reason(kind="played", text=text))
            points = actual if actual is not None else 0.0
            return PlayerWeek(player=player, week=week, status=GameStatus.PLAYED, projection=points,
                              if_active=points, provider_projection=provider, sources=sources, floor=points,
                              ceiling=points, play_probability=1.0 if actual is not None else 0.0,
                              matchup=matchup, actual_points=actual, reasons=reasons)

        base = provider
        if base is None:
            if player.weekly_projections:
                base = 0.0  # projected for other weeks but not this one: providers expect nothing
                reasons.append(Reason(kind="projection", text=f"No projection for week {week}.",
                                      effect=Effect.MINUS))
            else:
                base = player.ros_projection / max(1, self._games_left(player))
                reasons.append(Reason(kind="estimate",
                                      text="No weekly projection; estimated from rest-of-season points."))
        if provider is not None:
            reasons.append(Reason(kind="projection", text=_projection_text(provider, sources)))

        recent = _recent(history, week)
        ppg, n_games = (statistics.fmean(recent), len(recent)) if recent else (None, 0)
        if_active = base
        injury = player.injury_status
        if (
            ppg is not None and n_games >= MIN_FORM_GAMES and base > 0
            and player.position != Position.DST and injury not in ("O", "IR")
        ):
            factor = matchup.factor if matchup and matchup.factor else 1.0
            form = min(max(ppg * factor, (1 - FORM_CAP) * base), (1 + FORM_CAP) * base)
            if_active = (1 - self.form_weight) * base + self.form_weight * form
            if ppg > base * 1.15:
                reasons.append(Reason(kind="form", effect=Effect.PLUS,
                                      text=f"Hot: {ppg:.1f} points per game over the last {n_games} games."))
            elif ppg < base * 0.85:
                reasons.append(Reason(kind="form", effect=Effect.MINUS,
                                      text=f"Cold: {ppg:.1f} points per game over the last {n_games} games."))
            else:
                reasons.append(Reason(kind="form",
                                      text=f"Steady: {ppg:.1f} points per game over the last {n_games} games."))

        if matchup:
            reasons.extend(_matchup_reasons(player, matchup))

        play = PLAY_PROBABILITY.get(injury, 1.0) if injury else 1.0
        if injury:
            label = {"Q": "Questionable", "D": "Doubtful", "O": "Out", "IR": "On injured reserve"}.get(injury, injury)
            reasons.append(Reason(kind="injury", effect=Effect.MINUS,
                                  text=f"{label}: about {play:.0%} likely to play."))

        sd = self._spread(player.position, if_active, history, week)
        return PlayerWeek(
            player=player, week=week, status=GameStatus.UPCOMING,
            projection=round(if_active * play, 2), if_active=round(if_active, 2),
            provider_projection=provider, sources=sources,
            floor=round(max(0.0, if_active - Z_10 * sd), 1), ceiling=round(if_active + Z_10 * sd, 1),
            play_probability=play, matchup=matchup,
            recent_ppg=round(ppg, 2) if ppg is not None else None, recent_games=n_games, reasons=reasons,
        )

    def compare(
        self,
        players: Sequence[Player],
        week: int | None = None,
        slot: LineupSlot | None = None,
        starts: int = 1,
    ) -> StartSitAnalysis:
        week = self.check_week(week)
        if len({p.id for p in players}) != len(players):
            raise StartSitError("invalid_start_sit", "Each player can only be listed once.")
        if not 1 <= starts < len(players):
            raise StartSitError("invalid_start_sit", f"Starts must be between 1 and {len(players) - 1}.")
        slot = check_slot(players, slot)

        evaluated = sorted((self.evaluate(p, week) for p in players), key=_rank_key, reverse=True)
        start_probs, call_prob = self._simulate(evaluated, starts, seed=f"{week}:{','.join(p.id for p in players)}")
        for i, pw in enumerate(evaluated):
            pw.start_probability = round(start_probs[i], 3)
            pw.recommended = i < starts

        recommendation = Recommendation(
            start=[pw.player.id for pw in evaluated[:starts]],
            sit=[pw.player.id for pw in evaluated[starts:]],
            probability=round(call_prob, 3),
            confidence=_confidence(call_prob),
            summary=_summary(evaluated, starts, call_prob),
        )
        return StartSitAnalysis(
            season=self.season, week=week, slot=slot, starts=starts, players=evaluated,
            recommendation=recommendation, warnings=self._warnings(evaluated),
        )

    # ---------- internals ----------

    def _matchup(self, player: Player, game, week: int) -> Matchup:
        factor = (self.matchups.defense_factors.get(player.position) or {}).get(game.opponent)
        ranked = self.matchups.defense_rank(player.position, game.opponent)
        implied = self.matchups.implied_rank(player.team, week)
        return Matchup(
            opponent=game.opponent, home=game.home,
            factor=round(factor, 3) if factor is not None else None,
            rank=ranked[0] if ranked else None, teams=ranked[1] if ranked else 0,
            label=schedule_label(*ranked) if ranked else None,
            implied_total=game.implied_total, implied_rank=implied[0] if implied else None,
            favored_by=game.favored_by,
        )

    def _games_left(self, player: Player) -> int:
        start, last = self.current_week, self.weeks[1]
        if self.matchups.has_schedule(player.team):
            return sum(1 for w, g in self.matchups.schedule[player.team].items() if start <= w <= last and not g.played)
        return sum(1 for w in range(start, last + 1) if w != player.bye_week)

    @staticmethod
    def _spread(position: Position, mean: float, history: dict[int, float], week: int) -> float:
        prior = POSITION_CV[position] * mean
        games = [points for w, points in history.items() if w < week]
        if len(games) < MIN_SPREAD_GAMES:
            return prior
        weight = len(games) / (len(games) + SPREAD_PRIOR_GAMES)
        return math.sqrt(weight * statistics.pstdev(games) ** 2 + (1 - weight) * prior ** 2)

    def _simulate(self, ranked: list[PlayerWeek], starts: int, seed: str) -> tuple[list[float], float]:
        """(share of weeks each player finished in the top `starts`, share of weeks the closest
        starter beat the best benched player)."""
        rng = random.Random(seed)
        spreads = [
            0.0 if pw.status != GameStatus.UPCOMING else (pw.ceiling - pw.if_active) / Z_10 for pw in ranked
        ]
        in_top = [0] * len(ranked)
        call_right = 0
        for _ in range(self.simulations):
            scores = []
            for pw, sd in zip(ranked, spreads):
                plays = rng.random() < pw.play_probability if pw.status == GameStatus.UPCOMING else True
                scores.append(max(0.0, rng.gauss(pw.if_active, sd)) if plays else 0.0)
            order = sorted(range(len(ranked)), key=lambda i: scores[i], reverse=True)
            for i in order[:starts]:
                in_top[i] += 1
            if scores[starts - 1] >= scores[starts]:
                call_right += 1
        n = self.simulations
        return [count / n for count in in_top], call_right / n

    def _warnings(self, evaluated: list[PlayerWeek]) -> list[str]:
        warnings = []
        if not self.matchups.available:
            warnings.append("No matchup data yet (run `python -m app.data.collect`): using projections only.")
        for pw in evaluated:
            if pw.status == GameStatus.BYE:
                warnings.append(f"{pw.player.name} is on bye.")
            elif pw.status == GameStatus.PLAYED:
                warnings.append(f"{pw.player.name}'s game is already final.")
            elif pw.play_probability == 0:
                warnings.append(f"{pw.player.name} is not expected to play.")
        return warnings


def check_slot(players: Sequence[Player], slot: LineupSlot | None) -> LineupSlot:
    """The slot the players compete for: the one given (every player must fit it), or the
    narrowest slot that fits them all."""
    positions = {p.position for p in players}
    if slot is not None:
        misfits = [p.name for p in players if p.position not in SLOT_POSITIONS[slot]]
        if misfits:
            raise StartSitError("invalid_start_sit", f"Not eligible at {slot.value}: {', '.join(misfits)}.")
        return slot
    for candidate in LineupSlot:
        if positions <= SLOT_POSITIONS[candidate]:
            return candidate
    raise StartSitError("invalid_start_sit", "These players can't fill the same lineup spot.")


def _rank_key(pw: PlayerWeek) -> tuple:
    return (pw.projection, pw.floor, pw.player.ros_projection)


def _recent(history: dict[int, float], week: int) -> list[float]:
    weeks = sorted((w for w in history if week - FORM_WINDOW_WEEKS <= w < week), reverse=True)[:FORM_GAMES]
    return [history[w] for w in weeks]


def _projection_text(provider: float, sources: dict[str, float]) -> str:
    names = {"sleeper": "Sleeper", "espn": "ESPN"}
    detail = ", ".join(f"{names.get(k, k)} {v:.1f}" for k, v in sorted(sources.items()))
    return f"Projected {provider:.1f}" + (f" ({detail})." if len(sources) > 1 else ".")


def _matchup_reasons(player: Player, m: Matchup) -> list[Reason]:
    reasons = []
    where = "vs" if m.home else "at"
    if m.factor is not None and m.rank is not None:
        pct = round((m.factor - 1) * 100)
        target = "offenses" if player.position == Position.DST else f"{player.position.value}s"
        if player.position == Position.DST:
            text = f"{where} {m.opponent}: their offense scores {abs(pct)}% {'less' if pct >= 0 else 'more'} than average"
        else:
            text = f"{where} {m.opponent}: allows {abs(pct)}% {'more' if pct >= 0 else 'fewer'} points to {target} than average"
        effect = {"easy": Effect.PLUS, "hard": Effect.MINUS}.get(m.label, Effect.NEUTRAL)
        tier = {"easy": "an easy matchup", "hard": "a tough matchup"}.get(m.label, "an average matchup")
        reasons.append(Reason(kind="matchup", text=f"{text} ({tier}, {_ordinal(m.rank)} of {m.teams}).",
                              effect=effect))
    else:
        reasons.append(Reason(kind="matchup", text=f"{where} {m.opponent}."))
    if m.implied_total is not None and m.implied_rank is not None and player.position != Position.DST:
        effect = Effect.PLUS if m.implied_rank <= 8 else Effect.MINUS if m.implied_rank > 24 else Effect.NEUTRAL
        reasons.append(Reason(kind="vegas", effect=effect,
                              text=f"Vegas expects {player.team} to score {m.implied_total:.1f} "
                                   f"({_ordinal(m.implied_rank)} most this week)."))
    return reasons


def _confidence(probability: float) -> Confidence:
    if probability >= CONFIDENT_AT:
        return Confidence.CONFIDENT
    if probability >= LEAN_AT:
        return Confidence.LEAN
    return Confidence.TOSS_UP


def _summary(ranked: list[PlayerWeek], starts: int, probability: float) -> str:
    starters, bench = ranked[:starts], ranked[starts:]
    names = lambda group: _join([pw.player.name for pw in group])  # noqa: E731
    head = f"Start {names(starters)} over {names(bench)}."
    last, next_best = starters[-1], bench[0]
    if next_best.status == GameStatus.BYE or next_best.play_probability == 0:
        return f"{head} {next_best.player.name} isn't expected to play."
    gap = last.projection - next_best.projection
    why = f"{last.player.name} projects {gap:.1f} more points ({last.projection:.1f} to {next_best.projection:.1f})"
    if last.matchup and next_best.matchup and last.matchup.rank and next_best.matchup.rank:
        if last.matchup.rank < next_best.matchup.rank:
            why += " and has the easier matchup"
        elif last.matchup.rank > next_best.matchup.rank:
            why += " despite the tougher matchup"
    pct = round(probability * 100)
    tail = f"{last.player.name} outscores {next_best.player.name} in {pct}% of simulated weeks."
    if probability < LEAN_AT:
        return f"{head} {why}, but it's close: {tail}"
    return f"{head} {why}. {tail}"


def _join(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
