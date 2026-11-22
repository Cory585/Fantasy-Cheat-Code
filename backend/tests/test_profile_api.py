import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_local_player_data, get_player_repository, get_profile_service
from app.data import db
from app.data.profile_cache import ProfileCache
from app.data.repository import InMemoryPlayerRepository
from app.main import app
from app.models.player import Player, Position
from app.services.player_profile import LocalPlayerData, percentile
from app.services.profiles import ProfileService
from tests.test_espn_athletes import BIO, STATS

client = TestClient(app)

GIBBS = Player(id="9221", name="Jahmyr Gibbs", position=Position.RB, team="DET", espn_id=4429795)
DEFENSE = Player(id="DET", name="Lions D/ST", position=Position.DST, team="DET", espn_id=-16008)
NO_ESPN = Player(id="77", name="No Link", position=Position.WR, team="DET")


class Source:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = 0

    def bio(self, espn_id):
        self.calls += 1
        if self.fail:
            raise self.fail
        from app.data.sources.espn_athletes import parse_bio
        return parse_bio(BIO, espn_id)

    def stats(self, espn_id, league):
        from app.data.sources.espn_athletes import parse_stats
        return parse_stats(STATS)


@pytest.fixture
def local_db(tmp_path):
    conn = db.connect(tmp_path / "fantasy.db")
    db.upsert(conn, "players", [{"gsis_id": "00-1", "name": "Jahmyr Gibbs", "espn_id": 4429795, "pfr_id": "GibbJa00",
                                 "height": 69, "weight": 199, "draft_year": 2023, "draft_round": 1, "draft_pick": 12}])
    rows = [{"combine_id": f"x{i}", "season": 2020, "name": f"RB {i}", "position": "RB", "forty": f}
            for i, f in enumerate([4.40, 4.50, 4.60, 4.70])]
    rows.append({"combine_id": "GibbJa00", "season": 2023, "name": "Jahmyr Gibbs", "position": "RB",
                 "pfr_id": "GibbJa00", "school": "Alabama", "forty": 4.36})
    for row in rows:
        db.upsert(conn, "combine", [row])
    conn.close()
    return tmp_path / "fantasy.db"


def use(tmp_path, source, local_path=None, players=(GIBBS, DEFENSE, NO_ESPN)):
    service = ProfileService(source, ProfileCache(tmp_path / "profiles.db"))
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(list(players))
    app.dependency_overrides[get_profile_service] = lambda: service
    app.dependency_overrides[get_local_player_data] = lambda: LocalPlayerData(local_path)


def test_full_profile(tmp_path, local_db):
    use(tmp_path, Source(), local_db)
    resp = client.get("/players/9221/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["espn"]["status"] == "fresh"
    assert body["bio"]["college"] == "Alabama" and body["bio"]["height"] == "5' 9\""
    assert body["headshot_url"].startswith("https://a.espncdn.com/")
    assert body["nfl_stats"][0]["label"] == "Rushing" and body["college_stats"]
    (forty,) = body["combine"]["drills"]
    assert forty["value"] == 4.36 and forty["percentile"] == 100 and forty["compared_with"] == 5
    assert client.get("/players/9221/profile").json()["espn"]["status"] == "cached"


def test_synced_injury_status_wins_over_espn(tmp_path, local_db):
    # ESPN's bio says "Active"; the synced injury report says IR (#128).
    hurt = GIBBS.model_copy(update={"injury_status": "IR"})
    use(tmp_path, Source(), local_db, players=(hurt,))
    assert client.get("/players/9221/profile").json()["bio"]["status"] == "Injured reserve"


def test_espn_status_stands_without_an_injury(tmp_path, local_db):
    use(tmp_path, Source(), local_db)
    assert client.get("/players/9221/profile").json()["bio"]["status"] == "Active"


def test_espn_down_still_returns_local_data(tmp_path, local_db):
    use(tmp_path, Source(fail=httpx.ConnectError("down")), local_db)
    body = client.get("/players/9221/profile").json()
    assert body["espn"]["status"] == "unavailable" and body["espn"]["code"] == "upstream_error"
    assert body["espn"]["retry_after"] >= 1
    assert body["bio"]["height"] == "5' 9\"" and body["bio"]["draft"] == "2023: Rd 1, Pk 12"
    assert body["combine"]["school"] == "Alabama"
    assert body["nfl_stats"] == []


def test_defenses_and_unlinked_players_skip_espn(tmp_path):
    source = Source()
    use(tmp_path, source)
    for player_id in ["DET", "77"]:
        body = client.get(f"/players/{player_id}/profile").json()
        assert body["espn"]["status"] == "not_applicable"
    assert source.calls == 0


def test_unknown_player_is_404_and_never_reaches_espn(tmp_path):
    source = Source()
    use(tmp_path, source)
    resp = client.get("/players/999999/profile")
    assert resp.status_code == 404 and resp.json()["code"] == "player_not_found"
    assert source.calls == 0


def test_bad_ids_are_rejected(tmp_path):
    use(tmp_path, Source())
    assert client.get("/players/" + "9" * 40 + "/profile").status_code == 422


def test_profile_endpoint_is_strictly_rate_limited(tmp_path):
    use(tmp_path, Source())
    statuses = [client.get("/players/DET/profile").status_code for _ in range(12)]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


def test_missing_database_is_fine(tmp_path):
    use(tmp_path, Source(), tmp_path / "does-not-exist.db")
    body = client.get("/players/9221/profile").json()
    assert body["combine"] is None and body["bio"]["college"] == "Alabama"


@pytest.mark.parametrize("value, others, lower, expected", [
    (4.3, [4.3, 4.5, 4.6], True, 100),
    (4.6, [4.3, 4.5, 4.6], True, 0),
    (30, [30, 30, 40], False, 25),
    (4.4, [4.4], True, None),
])
def test_percentile(value, others, lower, expected):
    assert percentile(value, others, lower) == expected
