from fastapi import APIRouter, Depends

from app.api.errors import ApiError

from app.api.dependencies import get_trade_analyzer
from app.models.trade import TradeAnalysis, TradeRequest
from app.services.trade_analyzer import TradeAnalyzer, UnknownPlayerError

router = APIRouter(prefix="/trades", tags=["trades"])


@router.post("/analyze", response_model=TradeAnalysis)
def analyze_trade(
    request: TradeRequest,
    analyzer: TradeAnalyzer = Depends(get_trade_analyzer),
) -> TradeAnalysis:
    try:
        return analyzer.analyze(request)
    except UnknownPlayerError as e:
        raise ApiError(404, "player_not_found", str(e)) from e
    except ValueError as e:
        raise ApiError(422, "invalid_trade", str(e)) from e
