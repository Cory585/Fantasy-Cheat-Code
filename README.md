# Fantasy-Cheat-Code
Will include trade analyzer, kicker and defense predictions, tier listed players, start sit, better projections, draft masterclass

## Backend

Python + FastAPI. Currently implements a skeleton of the **trade analyzer**.

```
backend/
  app/
    main.py          FastAPI app + router registration
    config.py        settings (fair-trade threshold, defaults)
    models/          Pydantic models: Player, TradeRequest, TradeAnalysis
    data/            PlayerRepository interface + in-memory sample players
    services/        ValuationService (value over replacement), TradeAnalyzer
    api/             HTTP routes and dependency wiring
  tests/
```

### Run it

```
cd backend
python -m venv .venv
.venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest
```

Interactive docs at http://localhost:8000/docs.

### Endpoints

- `GET /health`
- `GET /players?position=RB` and `GET /players/{id}`
- `POST /trades/analyze`

```json
{
  "side_a": {"player_ids": ["te2"]},
  "side_b": {"player_ids": ["rb1"]},
  "league": {"scoring": "ppr", "num_teams": 12}
}
```

### Next steps

- Replace sample data with a real player/projection feed (implement `PlayerRepository`).
- Implement `TradeAnalyzer._lineup_impact` (optimal lineup before/after using `roster_ids`).
- Scoring-format, tier, and schedule-strength adjustments in `ValuationService`.
