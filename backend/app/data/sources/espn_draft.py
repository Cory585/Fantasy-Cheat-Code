"""A league's live draft from ESPN Fantasy.

Uses the same undocumented league endpoint the ESPN draft room reads. Public leagues need
no login. Private leagues need the `espn_s2` and `SWID` cookies from a browser signed in to
ESPN; they're sent only to ESPN, as a Cookie header, and never logged.

`draftDetail.picks` lists the picks made so far (in progress) or all of them (complete).
"""

import re
from typing import Literal

import httpx
from pydantic import BaseModel

from app.data.sources.espn import API
from app.data.sources.http import RateLimitedClient

DRAFT_VIEWS = ("mDraftDetail", "mSettings", "mTeams")

# Roster slot 21 is injured reserve, which isn't filled in the draft.
IR_SLOT = "21"

LEAGUE_ID_PATTERN = r"^[0-9]{1,12}$"
# espn_s2 is a long URL-encoded token; SWID is a GUID, usually in braces.
ESPN_S2_PATTERN = r"^[A-Za-z0-9%+/=_.\-]{20,2048}$"
SWID_PATTERN = r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"

DraftStatus = Literal["pre_draft", "in_progress", "complete"]


class EspnDraftPick(BaseModel):
    overall: int
    round: int
    round_pick: int
    team_id: int
    espn_player_id: int
    keeper: bool = False


class EspnDraftTeam(BaseModel):
    id: int
    name: str
    abbrev: str
    slot: int | None = None  # 1-based draft position, if the order is set


class EspnDraft(BaseModel):
    league_id: str
    season: int
    name: str
    status: DraftStatus
    draft_type: str
    team_count: int
    rounds: int
    teams: list[EspnDraftTeam]
    picks: list[EspnDraftPick]


class EspnCredentials(BaseModel):
    espn_s2: str
    swid: str

    def cookie_header(self) -> str:
        swid = self.swid.strip("{}").upper()
        return f"espn_s2={self.espn_s2}; SWID={{{swid}}}"


class LeagueNotFound(Exception):
    pass


class LeaguePrivate(Exception):
    """ESPN refused the request: the league is private and the cookies are missing or wrong."""


class EspnDraftSource:
    def __init__(self, client: RateLimitedClient):
        self._client = client

    def draft(self, season: int, league_id: str, credentials: EspnCredentials | None = None) -> EspnDraft:
        if not re.fullmatch(LEAGUE_ID_PATTERN, league_id):
            raise ValueError("league id must be digits")
        headers = {"Cookie": credentials.cookie_header()} if credentials else None
        try:
            data = self._client.get_json(
                f"{API}/seasons/{season}/segments/0/leagues/{league_id}",
                params=[("view", view) for view in DRAFT_VIEWS],
                headers=headers,
            )
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in (401, 403):
                raise LeaguePrivate(league_id) from None
            if status == 404:
                raise LeagueNotFound(league_id) from None
            raise
        return parse_draft(data, league_id, season)


def _team_name(team: dict) -> str:
    name = team.get("name") or " ".join(p for p in (team.get("location"), team.get("nickname")) if p)
    return (name or f"Team {team.get('id')}").strip()[:60]


def _rounds(settings: dict, picks: list[EspnDraftPick]) -> int:
    counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    rounds = sum(int(n) for slot, n in counts.items() if str(slot) != IR_SLOT and isinstance(n, int) and n > 0)
    if picks:
        rounds = max(rounds, max(p.round for p in picks))
    return rounds or 15


def parse_draft(data: dict, league_id: str, season: int) -> EspnDraft:
    """The league's draft from ESPN's league JSON (views mDraftDetail, mSettings, mTeams)."""
    if not isinstance(data, dict):
        raise ValueError("ESPN league response isn't an object")
    detail = data.get("draftDetail") or {}
    settings = data.get("settings") or {}
    draft_settings = settings.get("draftSettings") or {}

    picks = []
    for raw in detail.get("picks") or []:
        player_id = raw.get("playerId")
        # Unfilled pick slots carry a non-positive player id.
        if not isinstance(player_id, int) or player_id <= 0:
            continue
        picks.append(EspnDraftPick(
            overall=int(raw["overallPickNumber"]),
            round=int(raw["roundId"]),
            round_pick=int(raw["roundPickNumber"]),
            team_id=int(raw["teamId"]),
            espn_player_id=player_id,
            keeper=bool(raw.get("keeper")),
        ))
    picks.sort(key=lambda p: p.overall)

    order = [t for t in draft_settings.get("pickOrder") or [] if isinstance(t, int)]
    slots = {team_id: i + 1 for i, team_id in enumerate(order)}
    teams = [
        EspnDraftTeam(id=int(t["id"]), name=_team_name(t), abbrev=str(t.get("abbrev") or "")[:8],
                      slot=slots.get(int(t["id"])))
        for t in data.get("teams") or [] if isinstance(t, dict) and isinstance(t.get("id"), int)
    ]
    teams.sort(key=lambda t: (t.slot is None, t.slot or 0, t.id))

    if detail.get("drafted"):
        status: DraftStatus = "complete"
    elif detail.get("inProgress") or picks:
        status = "in_progress"
    else:
        status = "pre_draft"

    return EspnDraft(
        league_id=league_id,
        season=season,
        name=str(settings.get("name") or f"League {league_id}")[:80],
        status=status,
        draft_type=str(draft_settings.get("type") or "SNAKE"),
        team_count=len(order) or int(settings.get("size") or len(teams) or 0),
        rounds=_rounds(settings, picks),
        teams=teams,
        picks=picks,
    )
