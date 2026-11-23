import json

import httpx
import pytest

from radar.apify_client import ApifyClient, MissingApifyToken
from radar.db import Database
from radar.pipeline import OverBudget, execute_run
from radar.seed import WATCHLIST, load_example_payload


class ClienteFalso:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def fetch_reels(self, usernames, results_limit):
        self.calls.append((list(usernames), results_limit))
        return self.items

    def close(self):
        pass


def test_ingesta_con_fixture_sin_red(settings):
    db = Database(settings.db_path)
    for username in WATCHLIST:
        db.upsert_account(username, active=True)
    payload = load_example_payload()
    client = ClienteFalso(payload["items"])
    outcome = execute_run(
        db,
        settings,
        client=client,
        yes=True,
        skip_transcribe=True,
        skip_llm=True,
    )
    assert outcome.launched is True
    assert outcome.reels_saved == 16
    assert client.calls == [(sorted(WATCHLIST), 20)]
    rows = {row["short_code"]: row for row in db.list_reels()}
    assert rows["EJEMPLOJG4"]["is_example"] == 0
    assert rows["EJEMPLOJG4"]["is_outlier"] == 1
    assert rows["EJEMPLOJG4"]["video_play_count"] == 21000
    assert rows["EJEMPLOJG4"]["multiple"] == pytest.approx(10.5)
    assert rows["EJEMPLOJG4"]["video_view_count"] == 12345
    assert rows["EJEMPLOJG1"]["is_outlier"] == 0
    assert db.list_bank()[0].account == "jordigpt"


def test_sin_token_no_construye_cliente(settings, monkeypatch):
    db = Database(settings.db_path)
    db.upsert_account("jordigpt", active=True)

    def prohibido(*args, **kwargs):
        raise AssertionError("no hay que construir el cliente de Apify")

    monkeypatch.setattr("radar.pipeline.ApifyClient", prohibido)
    with pytest.raises(MissingApifyToken):
        execute_run(db, settings, yes=True, skip_transcribe=True, skip_llm=True)


def test_no_lanza_si_la_estimacion_supera_el_credito(settings):
    db = Database(settings.db_path)
    db.upsert_account("jordigpt", active=True)
    client = ClienteFalso([])
    with pytest.raises(OverBudget):
        execute_run(
            db,
            settings,
            client=client,
            yes=True,
            reels_per_account=100_000,
            skip_transcribe=True,
            skip_llm=True,
        )
    assert client.calls == []


def test_cliente_http_pide_metadatos_y_no_pone_el_token_en_la_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers["authorization"]
            seen["url"] = str(request.url)
            return httpx.Response(
                201,
                json={"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}},
            )
        assert request.url.path.endswith("/items")
        return httpx.Response(
            200,
            json=[
                {
                    "id": "1",
                    "url": "https://www.instagram.com/reel/ABC/",
                    "videoPlayCount": 10,
                    "ownerUsername": "jordigpt",
                    "audioUrl": "https://cdn.example/a.mp4",
                    "videoUrl": "https://cdn.example/v.mp4",
                }
            ],
        )

    client = ApifyClient("test-token", transport=httpx.MockTransport(handler))
    items = client.fetch_reels(["jordigpt"], 20)
    client.close()
    assert items[0]["videoPlayCount"] == 10
    assert seen["body"]["resultsLimit"] == 20
    assert seen["body"]["includeDownloadedVideo"] is False
    assert seen["body"]["includeTranscript"] is False
    assert seen["body"]["includeSharesCount"] is False
    assert seen["auth"] == "Bearer test-token"
    assert "test-token" not in seen["url"]
    assert "apify~instagram-reel-scraper" in seen["url"]
