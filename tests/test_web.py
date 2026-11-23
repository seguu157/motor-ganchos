import re

from fastapi.testclient import TestClient

from radar.seed import WATCHLIST
from radar.usernames import UsernameError, normalize_username
from radar.web import create_app


def test_la_interfaz_muestra_el_banco_de_ejemplo(settings):
    client = TestClient(create_app(settings))

    bank = client.get("/banco")
    assert bank.status_code == 200
    assert "10,5x" in bank.text
    assert "jordigpt" in bank.text
    assert "ejemplo" in bank.text
    assert "Datos de ejemplo" in bank.text
    for username in WATCHLIST:
        assert username in client.get("/cuentas").text

    match = re.search(r'href="/banco/(\d+)"', bank.text)
    assert match is not None
    detail = client.get(f"/banco/{match.group(1)}")
    assert detail.status_code == 200
    assert "[X]" in detail.text
    assert "10,5x" in detail.text

    listing = client.get("/reels")
    assert listing.status_code == 200
    assert "outlier" in listing.text

    radar = client.get("/radar")
    assert radar.status_code == 200
    assert "0,26" in radar.text
    assert "2,60" in radar.text

    blocked = client.post("/radar/lanzar", data={"confirmo": "1", "reels": "20"})
    assert blocked.status_code == 400
    assert "APIFY_TOKEN" in blocked.text
    assert "No se ha hecho ninguna petición" in blocked.text

    unconfirmed = client.post("/radar/lanzar", data={"reels": "20"})
    assert unconfirmed.status_code == 400
    assert "estimación" in unconfirmed.text

    markdown = client.get("/export/banco.md")
    assert "10,5x · @jordigpt · 2026-11-18" in markdown.text
    assert markdown.headers["content-disposition"].endswith("banco-ganchos.md")

    ajustes = client.get("/ajustes")
    assert ajustes.status_code == 200
    assert "desactivado" in ajustes.text
    assert "no definido" in ajustes.text


def test_normaliza_usuarios_de_perfil():
    assert normalize_username("@JordiGPT") == "jordigpt"
    assert normalize_username("https://www.instagram.com/dani_llamazares/reels/") == "dani_llamazares"
    with __import__("pytest").raises(UsernameError):
        normalize_username("https://www.instagram.com/reel/ABC123/")
