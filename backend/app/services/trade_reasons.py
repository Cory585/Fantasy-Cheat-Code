"""Why one side of a trade comes out ahead or behind, in plain language.

Each reason compares what a side receives with what it sends: total value, who gets the best
player, roster spots, starters, value by position, remaining schedules, recent form, injuries,
and how sure the projections are. Reasons are ordered by how much they usually matter, and
each is marked plus (helps this side), minus (hurts it), or neutral.
"""

from collections import defaultdict

from app.models.trade import Effect, PlayerValuation, Reason

MAX_REASONS = 10
SCHEDULE_GAP = 0.04  # average schedule factors this far apart (4%) are worth a mention
POSITION_GAP = 5.0  # value points at one position worth a mention


def _points(value: float) -> str:
    return f"{value:.0f}"


def _names(players: list[PlayerValuation]) -> str:
    names = [p.player.name for p in players]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _describe(v: PlayerValuation) -> str:
    detail = ", ".join(part for part in (v.tier, f"{_points(v.value)} value") if part)
    return f"{v.player.name} ({detail})"


def _form(v: PlayerValuation) -> int:
    """+1 running above projections, -1 below, 0 neither (same 10% rule as the player notes)."""
    o = v.outlook
    if o is None or o.form_projection is None or o.recent_ppg is None or not o.provider_projection:
        return 0
    trend = o.form_projection - o.provider_projection
    if abs(trend) < 0.1 * o.provider_projection:
        return 0
    return 1 if trend > 0 else -1


def _uncertain(v: PlayerValuation) -> bool:
    return any(c.startswith("Projections disagree") for c in v.concerns)


def side_reasons(sends: list[PlayerValuation], receives: list[PlayerValuation]) -> list[Reason]:
    """Why the side that sends `sends` and receives `receives` wins or loses, most important first."""
    reasons: list[Reason] = []
    given = sum(v.value for v in sends)
    received = sum(v.value for v in receives)
    net = received - given

    # Overall value.
    if abs(net) < 0.5:
        reasons.append(Reason(kind="value", effect=Effect.NEUTRAL,
                              text=f"Sends and receives the same value ({_points(given)} points)"))
    else:
        reasons.append(Reason(
            kind="value", effect=Effect.PLUS if net > 0 else Effect.MINUS,
            text=f"Receives {_points(received)} points of value for {_points(given)} sent "
                 f"({'+' if net > 0 else '−'}{_points(abs(net))})"))

    # The best player usually wins the trade, especially when the other side sends more players.
    everyone = sends + receives
    if len(everyone) > 2:
        best = max(everyone, key=lambda v: v.value)
        if best.value > 0:
            if best in receives:
                reasons.append(Reason(kind="best_player", effect=Effect.PLUS,
                                      text=f"Gets the best player in the deal: {_describe(best)}"))
            else:
                reasons.append(Reason(kind="best_player", effect=Effect.MINUS,
                                      text=f"Gives up the best player in the deal: {_describe(best)}"))

    # Roster spots: consolidating frees a spot for the waiver wire; taking back more costs a drop.
    spots = len(sends) - len(receives)
    if spots > 0:
        reasons.append(Reason(
            kind="roster_spots", effect=Effect.PLUS,
            text=f"Consolidates {len(sends)} players into {len(receives)}, opening "
                 f"{spots} roster spot{'s' if spots > 1 else ''} for a waiver pickup"))
    elif spots < 0:
        reasons.append(Reason(
            kind="roster_spots", effect=Effect.MINUS,
            text=f"Takes back {len(receives)} players for {len(sends)}: adds depth, but "
                 f"{-spots} player{'s' if spots < -1 else ''} must be dropped to make room"))

    # Starters in and out.
    starters_out = sum(1 for v in sends if v.starter)
    starters_in = sum(1 for v in receives if v.starter)
    if starters_in != starters_out:
        reasons.append(Reason(
            kind="starters", effect=Effect.PLUS if starters_in > starters_out else Effect.MINUS,
            text=f"Gets {starters_in} starter{'s' if starters_in != 1 else ''} back for "
                 f"{starters_out} sent, in a league with these lineup slots"))

    # Value by position: where the side gets stronger and where it gets thinner.
    by_position: dict[str, float] = defaultdict(float)
    for v in receives:
        by_position[v.player.position.value] += v.value
    for v in sends:
        by_position[v.player.position.value] -= v.value
    if len(by_position) > 1:
        gain = max(by_position, key=by_position.get)
        loss = min(by_position, key=by_position.get)
        if by_position[gain] >= POSITION_GAP:
            reasons.append(Reason(kind="positions", effect=Effect.PLUS,
                                  text=f"Stronger at {gain}: +{_points(by_position[gain])} points of value"))
        if by_position[loss] <= -POSITION_GAP:
            reasons.append(Reason(kind="positions", effect=Effect.MINUS,
                                  text=f"Thinner at {loss}: −{_points(-by_position[loss])} points of value"))

    # Remaining schedules (players with schedule data only).
    def schedule(players):
        factors = [v.outlook.sos_factor for v in players if v.outlook and v.outlook.sos_factor]
        return sum(factors) / len(factors) if factors else None
    s_in, s_out = schedule(receives), schedule(sends)
    if s_in is not None and s_out is not None and abs(s_in - s_out) >= SCHEDULE_GAP:
        easier = s_in > s_out
        reasons.append(Reason(
            kind="schedule", effect=Effect.PLUS if easier else Effect.MINUS,
            text=f"Players coming in have {'easier' if easier else 'harder'} remaining schedules "
                 f"({abs(s_in - s_out):.0%} {'more' if easier else 'fewer'} points allowed by their opponents)"))

    # Recent form.
    hot_in = [v for v in receives if _form(v) > 0]
    hot_out = [v for v in sends if _form(v) > 0]
    cold_in = [v for v in receives if _form(v) < 0]
    if hot_in:
        reasons.append(Reason(kind="form", effect=Effect.PLUS,
                              text=f"Gets {_names(hot_in)}, whose recent form points above projections"))
    if hot_out:
        reasons.append(Reason(kind="form", effect=Effect.MINUS,
                              text=f"Gives up {_names(hot_out)}, whose recent form points above projections"))
    if cold_in:
        reasons.append(Reason(kind="form", effect=Effect.MINUS,
                              text=f"Gets {_names(cold_in)}, whose recent form points below projections"))

    # Injuries.
    hurt_in = [v for v in receives if v.player.injury_status]
    hurt_out = [v for v in sends if v.player.injury_status]
    if hurt_in:
        reasons.append(Reason(
            kind="injury", effect=Effect.MINUS,
            text="Takes on injury risk: " + ", ".join(f"{v.player.name} ({v.player.injury_status})" for v in hurt_in)))
    if hurt_out:
        reasons.append(Reason(
            kind="injury", effect=Effect.PLUS,
            text="Moves injury risk: " + ", ".join(f"{v.player.name} ({v.player.injury_status})" for v in hurt_out)))

    # How sure the projections are.
    shaky_in = [v for v in receives if _uncertain(v)]
    if shaky_in:
        reasons.append(Reason(kind="uncertainty", effect=Effect.MINUS,
                              text=f"Sleeper and ESPN disagree on {_names(shaky_in)}, so the return is less certain"))

    return reasons[:MAX_REASONS]
