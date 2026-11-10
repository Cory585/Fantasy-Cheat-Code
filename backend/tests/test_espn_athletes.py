import httpx
import pytest

from app.data.sources.espn_athletes import (
    AthleteNotFound,
    EspnAthleteSource,
    parse_bio,
    parse_stats,
    valid_espn_id,
)
from app.data.sources.http import RateLimitedClient

BIO = {
    "athlete": {
        "displayName": "Jahmyr Gibbs",
        "position": {"abbreviation": "RB"},
        "team": {"abbreviation": "DET"},
        "displayJersey": "#0",
        "displayHeight": "5' 9\"",
        "displayWeight": "202 lbs",
        "age": 24,
        "displayBirthPlace": "Dalton, GA",
        "displayExperience": "4th Season",
        "college": {"name": "Alabama"},
        "displayDraft": "2023: Rd 1, Pk 12 (DET)",
        "status": {"name": "Active"},
        "headshot": {"href": "https://a.espncdn.com/i/headshots/nfl/players/full/4429795.png"},
    }
}

STATS = {
    "teams": {"detroit-lions": {"abbreviation": "DET"}},
    "categories": [{
        "name": "rushing",
        "displayName": "Rushing",
        "labels": ["GP", "CAR", "YDS"],
        "displayNames": ["Games Played", "Rushing Attempts", "Rushing Yards"],
        "statistics": [
            {"teamSlug": "detroit-lions", "season": {"year": 2023}, "stats": ["15", "182", "945"]},
            {"teamSlug": "unknown", "season": {"year": 2024}, "stats": ["17", "250"]},  # short row
        ],
        "totals": ["57", "852", "4,427"],
    }],
}


def test_parse_bio():
    bio = parse_bio(BIO, 4429795)
    assert bio["name"] == "Jahmyr Gibbs" and bio["position"] == "RB" and bio["team"] == "DET"
    assert bio["height"] == "5' 9\"" and bio["weight"] == "202 lbs" and bio["age"] == 24
    assert bio["college"] == "Alabama" and bio["draft"].startswith("2023")
    assert bio["headshot_url"].startswith("https://a.espncdn.com/")


def test_parse_bio_distrusts_odd_values():
    data = {"athlete": {
        "displayName": "x" * 500 + "\n\t<script>",
        "age": "old",
        "position": "not a dict",
        "headshot": {"href": "https://evil.example/track.png"},
    }}
    bio = parse_bio(data, 7)
    assert len(bio["name"]) == 120 and "\n" not in bio["name"]
    assert bio["age"] is None and bio["position"] is None
    assert bio["headshot_url"] == "https://a.espncdn.com/i/headshots/nfl/players/full/7.png"


def test_parse_bio_without_athlete_is_not_found():
    with pytest.raises(AthleteNotFound):
        parse_bio({}, 1)


def test_parse_stats():
    (rushing,) = parse_stats(STATS)
    assert rushing["label"] == "Rushing"
    assert [c["label"] for c in rushing["columns"]] == ["GP", "CAR", "YDS"]
    first, second = rushing["seasons"]
    assert first == {"year": 2023, "team": "DET", "values": [15, 182, 945]}
    assert second["team"] is None and second["values"] == [17, 250, None]
    assert rushing["totals"] == [57, 852, 4427]


def test_parse_stats_tolerates_garbage():
    assert parse_stats({"categories": "nope"}) == []
    assert parse_stats({"categories": [{"labels": []}, 5, None]}) == []


@pytest.mark.parametrize("value", [0, -1, True, "123", 10**12, None])
def test_invalid_espn_ids(value):
    assert not valid_espn_id(value)


def _source(handler):
    client = RateLimitedClient(min_interval=0, max_retries=0, transport=httpx.MockTransport(handler))
    return EspnAthleteSource(client)


def test_fetches_from_the_expected_urls():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=STATS if request.url.path.endswith("/stats") else BIO)

    source = _source(handler)
    assert source.bio(4429795)["name"] == "Jahmyr Gibbs"
    assert source.stats(4429795, "college")[0]["label"] == "Rushing"
    assert seen == [
        "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/4429795",
        "https://site.web.api.espn.com/apis/common/v3/sports/football/college-football/athletes/4429795/stats",
    ]


def test_404_is_not_found_and_other_errors_raise():
    with pytest.raises(AthleteNotFound):
        _source(lambda r: httpx.Response(404)).bio(1)
    with pytest.raises(httpx.HTTPStatusError):
        _source(lambda r: httpx.Response(500)).bio(1)
    with pytest.raises(ValueError):
        _source(lambda r: httpx.Response(200, json=[1, 2])).bio(1)


def test_rejects_bad_ids_before_any_request():
    def handler(request):
        raise AssertionError("no request should be made")

    with pytest.raises(ValueError):
        _source(handler).bio(-5)
    with pytest.raises(ValueError):
        _source(handler).stats(5, "../../admin")
