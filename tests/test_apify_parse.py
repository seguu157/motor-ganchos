import json
from pathlib import Path

from radar.apify_parse import build_actor_input, parse_item, parse_payload

FIXTURES = Path(__file__).resolve().parents[1] / "radar" / "fixtures"


def test_parsea_fixture_con_forma_del_actor():
    payload = json.loads((FIXTURES / "apify_items.example.json").read_text(encoding="utf-8"))
    assert payload["example"] is True
    result = parse_payload(payload)
    assert result.errors == []
    by_code = {reel.short_code: reel for reel in result.reels}
    viral = by_code["EJEMPLOJG4"]
    assert viral.video_play_count == 21000
    assert viral.video_view_count == 12345
    assert viral.likes_count == 900
    assert viral.comments_count == 40
    assert viral.published_at.startswith("2026-11-18")
    assert viral.caption.startswith("[EJEMPLO]")
    assert viral.url == "https://www.instagram.com/reel/EJEMPLOJG4/"
    assert viral.video_url.endswith("EJEMPLOJG4.mp4")
    assert viral.audio_url.endswith("EJEMPLOJG4.mp4")
    assert viral.owner_username == "jordigpt"
    assert len(result.reels) == 16


def test_acepta_una_lista_cruda_como_la_del_dataset():
    raw = json.loads((FIXTURES / "apify_aliases.example.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    result = parse_payload(raw)
    reel = result.reels[0]
    assert reel.likes_count == 15
    assert reel.comments_count == 3
    assert reel.published_at.startswith("2026-11-02")
    assert reel.video_play_count == 50


def test_prioriza_los_nombres_oficiales_frente_al_alias():
    reel = parse_item(
        {
            "id": "1",
            "url": "https://www.instagram.com/reel/ABC/",
            "likesCount": 7,
            "likes": 99,
            "commentsCount": 4,
            "comments": 80,
            "timestamp": "2026-11-01T00:00:00.000Z",
            "date": "2020-01-01",
            "videoPlayCount": 10,
            "videoViewCount": 999999,
            "ownerUsername": "@JordiGPT",
        }
    )
    assert reel.likes_count == 7
    assert reel.comments_count == 4
    assert reel.published_at.startswith("2026-11-01")
    assert reel.video_play_count == 10
    assert reel.video_view_count == 999999
    assert reel.owner_username == "jordigpt"


def test_no_convierte_decimales_en_reproducciones_y_salta_basura():
    result = parse_payload(
        [
            {"caption": "sin identidad"},
            {
                "id": "2",
                "url": "https://www.instagram.com/reel/SOLO/",
                "videoPlayCount": "12",
            },
            {
                "id": "3",
                "url": "https://www.instagram.com/reel/RARO/",
                "videoPlayCount": "10,5",
                "likesCount": -3,
            },
        ]
    )
    assert any("sin identidad" not in error and "falta id" in error for error in result.errors)
    by_id = {reel.instagram_id: reel for reel in result.reels}
    assert by_id["2"].video_play_count == 12
    assert by_id["3"].video_play_count is None
    assert by_id["3"].likes_count is None


def test_entrada_del_actor_solo_pide_metadatos():
    payload = build_actor_input(["@jordigpt", "dani_llamazares"], 20)
    assert payload == {
        "username": ["jordigpt", "dani_llamazares"],
        "resultsLimit": 20,
        "skipPinnedPosts": False,
        "skipTrialReels": False,
        "includeSharesCount": False,
        "includeTranscript": False,
        "includeDownloadedVideo": False,
    }
