"""Trade analyzer: compares the value each side gives and receives."""

from app.config import settings
from app.data.repository import PlayerRepository
from app.models.trade import (
    BalanceSuggestion,
    LeagueSettings,
    PlayerValuation,
    SideAnalysis,
    TradeAnalysis,
    TradeRequest,
    TradeSide,
    Verdict,
)
from app.services.valuation import ValuationService


class UnknownPlayerError(ValueError):
    def __init__(self, player_ids: list[str]):
        super().__init__(f"Unknown player id(s): {', '.join(player_ids)}")
        self.player_ids = player_ids


class TradeAnalyzer:
    def __init__(self, repo: PlayerRepository, valuation: ValuationService):
        self._repo = repo
        self._valuation = valuation

    def analyze(self, request: TradeRequest) -> TradeAnalysis:
        self._validate(request)
        league = request.league

        a_gives = self._value_side(request.side_a, league)
        b_gives = self._value_side(request.side_b, league)
        a_total = sum(v.value for v in a_gives)
        b_total = sum(v.value for v in b_gives)

        side_a = self._side_analysis(request.side_a, a_gives, given=a_total, received=b_total, league=league)
        side_b = self._side_analysis(request.side_b, b_gives, given=b_total, received=a_total, league=league)

        # Round before judging so the verdict matches the reported percentage and
        # float noise (e.g. 10.000000000000002) can't push an exact 10% gap past the threshold.
        diff_pct = round(self._difference_pct(a_total, b_total), 1)
        verdict = self._verdict(a_total, b_total, diff_pct)

        return TradeAnalysis(
            verdict=verdict,
            value_difference_pct=diff_pct,
            side_a=side_a,
            side_b=side_b,
            summary=self._summary(verdict, diff_pct),
            warnings=self._warnings(a_gives + b_gives, request),
            balance=self._balance(request, verdict, a_total, b_total),
        )

    # --- helpers -------------------------------------------------------------

    def _balance(
        self, request: TradeRequest, verdict: Verdict, a_total: float, b_total: float, limit: int = 5
    ) -> BalanceSuggestion | None:
        """Players closest in value to the gap that the winning side could add.

        The winner is the side that receives more, so it's the one that should give more.
        If the winner's `roster_ids` were sent, suggestions come from that roster; otherwise
        from every player not already in the trade.
        """
        if verdict == Verdict.FAIR:
            return None
        side_key, side = ("a", request.side_a) if verdict == Verdict.FAVORS_A else ("b", request.side_b)
        gap = abs(a_total - b_total)
        in_trade = set(request.side_a.player_ids) | set(request.side_b.player_ids)
        if side.roster_ids:
            pool = [p for p in self._repo.get_many(side.roster_ids) if p.id not in in_trade]
        else:
            pool = [p for p in self._repo.list() if p.id not in in_trade]
        valued = [v for v in (self._valuation.value(p, request.league) for p in pool) if v.value > 0]
        valued.sort(key=lambda v: abs(v.value - gap))
        if not valued:
            return None
        return BalanceSuggestion(side=side_key, value_gap=round(gap, 2), players=valued[:limit])

    def _validate(self, request: TradeRequest) -> None:
        ids = request.side_a.player_ids + request.side_b.player_ids
        missing = [pid for pid in ids if self._repo.get(pid) is None]
        if missing:
            raise UnknownPlayerError(missing)
        for side in (request.side_a, request.side_b):
            dupes = {pid for pid in side.player_ids if side.player_ids.count(pid) > 1}
            if dupes:
                raise ValueError(f"Player(s) listed more than once: {', '.join(sorted(dupes))}")
        overlap = set(request.side_a.player_ids) & set(request.side_b.player_ids)
        if overlap:
            raise ValueError(f"Player(s) on both sides of trade: {', '.join(sorted(overlap))}")

    def _value_side(self, side: TradeSide, league: LeagueSettings) -> list[PlayerValuation]:
        return [self._valuation.value(p, league) for p in self._repo.get_many(side.player_ids)]

    def _side_analysis(
        self,
        side: TradeSide,
        gives: list[PlayerValuation],
        given: float,
        received: float,
        league: LeagueSettings,
    ) -> SideAnalysis:
        return SideAnalysis(
            team_name=side.team_name,
            gives=gives,
            total_value_given=round(given, 2),
            total_value_received=round(received, 2),
            net_value=round(received - given, 2),
            lineup_impact=self._lineup_impact(side, league),
        )

    def _lineup_impact(self, side: TradeSide, league: LeagueSettings) -> float | None:
        """TODO: compute optimal starting lineup before/after the trade using roster_ids.

        This is where roster fit matters: a 2-for-1 that clogs your bench may be
        worth less than its raw value suggests.
        """
        return None

    @staticmethod
    def _difference_pct(a_total: float, b_total: float) -> float:
        larger = max(a_total, b_total)
        return 0.0 if larger == 0 else abs(a_total - b_total) / larger * 100

    @staticmethod
    def _verdict(a_total: float, b_total: float, diff_pct: float) -> Verdict:
        if diff_pct <= settings.fair_trade_threshold_pct:
            return Verdict.FAIR
        # Side A *receives* B's players, so A wins when B gives more value.
        return Verdict.FAVORS_A if b_total > a_total else Verdict.FAVORS_B

    @staticmethod
    def _summary(verdict: Verdict, diff_pct: float) -> str:
        if verdict == Verdict.FAIR:
            return f"Roughly even trade ({diff_pct:.1f}% value difference)."
        winner = "Side A" if verdict == Verdict.FAVORS_A else "Side B"
        return f"{winner} wins this trade by {diff_pct:.1f}% in value."

    @staticmethod
    def _warnings(valuations: list[PlayerValuation], request: TradeRequest) -> list[str]:
        warnings = [
            f"{v.player.name} is listed as {v.player.injury_status}."
            for v in valuations
            if v.player.injury_status
        ]
        if len(request.side_a.player_ids) != len(request.side_b.player_ids):
            warnings.append(
                "Uneven player counts: consider roster spots and the drop needed to make room."
            )
        return warnings
