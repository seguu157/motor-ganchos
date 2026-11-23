from datetime import date, timedelta

from radar.apify_parse import parse_item
from radar.db import Database
from radar.outliers import PlaySample, detect_outliers, median_plays
from radar.pipeline import ingest_reels


def test_mediana_impar_y_par():
    assert median_plays([400, 800, 1000, 1200, 5000]) == 1000
    assert median_plays([1000, 2000, 2000, 21000]) == 2000


def test_umbral_inclusivo_y_por_cuenta():
    samples = [
        PlaySample("a1", "alfa", 100, "2026-11-01"),
        PlaySample("a2", "alfa", 100, "2026-11-02"),
        PlaySample("a3", "alfa", 200, "2026-11-03"),
        PlaySample("b1", "beta", 10000, "2026-11-01"),
        PlaySample("b2", "beta", 10000, "2026-11-02"),
        PlaySample("b3", "beta", 19900, "2026-11-03"),
    ]
    scores = {item.reel_key: item for item in detect_outliers(samples)}
    assert scores["a3"].is_outlier is True
    assert scores["a3"].multiple == 2
    assert scores["a1"].is_outlier is False
    assert scores["b3"].is_outlier is False
    assert scores["b3"].median_plays == 10000


def test_ventana_de_ultimos_20_ignora_el_resto():
    samples = []
    start = date(2026, 6, 1)
    for index in range(20):
        plays = 1000 if index == 0 else 100
        published = (start + timedelta(days=index)).isoformat()
        samples.append(PlaySample(f"nuevo-{index}", "cuenta", plays, published))
    samples.append(PlaySample("viejo", "cuenta", 5_000_000, "2020-01-01"))
    scores = {item.reel_key: item for item in detect_outliers(samples)}
    assert "viejo" not in scores
    assert scores["nuevo-0"].is_outlier is True
    assert scores["nuevo-0"].median_plays == 100
    assert scores["nuevo-0"].sample_size == 20
    assert scores["nuevo-1"].is_outlier is False


def test_sin_reproducciones_no_entran_en_la_mediana():
    samples = [
        PlaySample("a", "cuenta", None, "2026-11-03"),
        PlaySample("b", "cuenta", 10, "2026-11-02"),
        PlaySample("c", "cuenta", 30, "2026-11-01"),
    ]
    scores = {item.reel_key: item for item in detect_outliers(samples)}
    assert "a" not in scores
    assert scores["b"].median_plays == 20
    assert scores["b"].sample_size == 2


def test_un_solo_reel_no_es_outlier_y_mediana_cero():
    alone = detect_outliers([PlaySample("solo", "cuenta", 999, "2026-11-01")])
    assert alone[0].multiple == 1
    assert alone[0].is_outlier is False

    zeros = {
        item.reel_key: item
        for item in detect_outliers(
            [
                PlaySample("z1", "cuenta", 0, "2026-11-01"),
                PlaySample("z2", "cuenta", 0, "2026-11-02"),
                PlaySample("z3", "cuenta", 8, "2026-11-03"),
            ]
        )
    }
    assert zeros["z1"].is_outlier is False
    assert zeros["z3"].is_outlier is True
    assert zeros["z3"].multiple == float("inf")


def test_todos_iguales_no_hay_outlier():
    samples = [PlaySample(str(i), "cuenta", 40, f"2026-11-0{i}") for i in range(1, 5)]
    assert all(item.is_outlier is False for item in detect_outliers(samples))


def test_la_base_no_marca_un_reel_fuera_de_la_ventana(tmp_path):
    db = Database(tmp_path / "radar.sqlite")
    start = date(2026, 3, 1)
    reels = []
    for index in range(20):
        published = (start + timedelta(days=index)).isoformat()
        reels.append(
            parse_item(
                {
                    "id": f"nuevo-{index}",
                    "url": f"https://www.instagram.com/reel/NUEVO{index}/",
                    "videoPlayCount": 1000 if index == 0 else 100,
                    "timestamp": published,
                    "ownerUsername": "ventana",
                    "caption": "[EJEMPLO] no es un reel real",
                }
            )
        )
    reels.append(
        parse_item(
            {
                "id": "viejo",
                "url": "https://www.instagram.com/reel/VIEJO/",
                "videoPlayCount": 9_000_000,
                "timestamp": "2019-01-01",
                "ownerUsername": "ventana",
                "caption": "[EJEMPLO] no es un reel real",
            }
        )
    )
    ingest_reels(db, reels, is_example=True, run_id=None)
    rows = {row["instagram_id"]: row for row in db.list_reels()}
    assert rows["viejo"]["is_outlier"] == 0
    assert rows["nuevo-0"]["is_outlier"] == 1
    assert rows["nuevo-0"]["median_plays"] == 100
    assert "viejo" not in {entry.short_code for entry in db.list_bank()}
    assert any(entry.reel_id == rows["nuevo-0"]["id"] for entry in db.list_bank())
