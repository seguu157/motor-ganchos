import inspect
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from radar.apify_client import ApifyClient, ApifyQuotaError, MissingApifyToken
from radar.apify_parse import parse_item
from radar.db import Database
from radar.pipeline import OverBudget, describe_plan, execute_selected_run, ingest_reels
from radar.sources.instaloader_source import (
    ACCOUNT_DELAY_SECONDS,
    POST_DELAY_SECONDS,
    InstagramBlocked,
    InstaloaderSource,
    as_instagram_block,
    login_secondary_account,
    post_to_item,
)
from radar.sources.select import block_reason, plan_source
from tests.conftest import make_settings


class Profile:
    def __init__(self, posts=None, error=None):
        self._posts = list(posts or [])
        self._error = error

    def get_reels(self):
        if self._error is not None:
            raise self._error
        return iter(self._posts)


class TooManyRequestsException(Exception):
    pass


class ProfileMissing(Exception):
    pass


def _post(shortcode, plays, views, day):
    return SimpleNamespace(
        shortcode=shortcode,
        mediaid=shortcode,
        caption="[EJEMPLO] texto ficticio, no es un reel real",
        likes=8,
        comments=2,
        video_play_count=plays,
        video_view_count=views,
        date_utc=datetime(2026, 11, day, tzinfo=timezone.utc),
        video_url=f"https://example.invalid/{shortcode}.mp4",
        owner_username="jordigpt",
        video_duration=12.0,
    )


class FuenteFalsa:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.calls = []
        self.warnings = []
        self.skipped = []

    def fetch_reels(self, usernames, results_limit):
        self.calls.append((list(usernames), results_limit))
        if self.error is not None:
            raise self.error
        return self.items

    def close(self):
        pass


def test_auto_elige_apify_solo_si_hay_token_y_cabe(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, APIFY_TOKEN="token-de-prueba")
    cuentas = ["jordigpt", "dani_llamazares"]
    cabe = plan_source(settings, cuentas, 20)
    assert cabe.effective == "apify"
    assert cabe.estimate.estimated_usd == Decimal("0.10")

    sin_token = make_settings(monkeypatch, tmp_path, APIFY_TOKEN=None)
    respaldo = plan_source(sin_token, cuentas, 20)
    assert respaldo.effective == "instaloader"
    assert respaldo.estimate.estimated_usd == Decimal("0.00")
    assert "0,00 USD" in describe_plan(respaldo)

    caro = plan_source(settings, cuentas, 20_000)
    assert caro.effective == "instaloader"
    forzado = plan_source(settings, cuentas, 20_000, allow_over_budget=True)
    assert forzado.effective == "apify"

    explicito = plan_source(settings, cuentas, 20, requested="instaloader")
    assert explicito.effective == "instaloader"
    assert plan_source(sin_token, cuentas, 20, requested="apify").effective == "apify"


def test_instaloader_limita_cuentas_y_el_bloque_no_pide_red(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, RADAR_IG_MAX_ACCOUNTS="2")
    plan = plan_source(settings, ["a", "b", "c"], 20, requested="instaloader")
    assert plan.usernames == ["a", "b"]
    assert plan.skipped == ["c"]
    assert plan.estimate.estimated_usd == Decimal("0.00")
    assert block_reason(settings, plan, allow_over_budget=False).startswith("Falta APIFY_TOKEN")
    assert "No se ha hecho ninguna petición" in block_reason(settings, plan, allow_over_budget=False)


def test_mapeo_de_instaloader_y_outlier_con_el_mismo_modelo(tmp_path):
    plays = [1000, 2000, 2000, 21000]
    reels = []
    for index, plays_n in enumerate(plays):
        item = post_to_item(_post(f"IG{index}", plays_n, 7, index + 1), "jordigpt")
        assert item["videoPlayCount"] == plays_n
        assert item["videoViewCount"] == 7
        assert item["url"] == f"https://www.instagram.com/reel/IG{index}/"
        assert item["videoUrl"].endswith(f"IG{index}.mp4")
        reels.append(parse_item(item))
    solo_vistas = post_to_item(_post("VISTAS", None, 50, 10), "jordigpt")
    assert solo_vistas["videoPlayCount"] == 50
    assert solo_vistas["likesCount"] == 8
    assert solo_vistas["commentsCount"] == 2

    db = Database(tmp_path / "radar.sqlite")
    ingest_reels(db, reels, is_example=True, run_id=None)
    rows = {row["short_code"]: row for row in db.list_reels()}
    assert rows["IG3"]["is_example"] == 1
    assert rows["IG3"]["is_outlier"] == 1
    assert rows["IG3"]["multiple"] == pytest.approx(10.5)
    assert rows["IG0"]["is_outlier"] == 0


def test_pausas_y_parada_sin_reintentos(tmp_path):
    sleeps = []
    spans = []

    def uniform(low, high):
        spans.append((low, high))
        return low

    profiles = {
        "uno": Profile([_post("A1", 10, 1, 1), _post("A2", 12, 1, 2)]),
        "dos": Profile([_post("B1", 20, 1, 3), _post("B2", 22, 1, 4)]),
    }
    source = InstaloaderSource(
        None,
        tmp_path / "no-se-usa.session",
        profile_lookup=lambda name: profiles[name],
        sleeper=sleeps.append,
        uniform=uniform,
    )
    items = source.fetch_reels(["uno", "dos"], 20)
    assert [item["shortCode"] for item in items] == ["A1", "A2", "B1", "B2"]
    assert spans == [POST_DELAY_SECONDS, ACCOUNT_DELAY_SECONDS, POST_DELAY_SECONDS]
    assert sleeps == [2, 20, 2]
    assert ACCOUNT_DELAY_SECONDS == (20, 60)

    calls = []

    def lookup(name):
        calls.append(name)
        if name == "dos":
            raise TooManyRequestsException("429 too many requests")
        return Profile([_post("A1", 10, 1, 1)])

    blocked = InstaloaderSource(
        None,
        tmp_path / "no-se-usa.session",
        profile_lookup=lookup,
        sleeper=lambda _delay: None,
        uniform=lambda low, _high: low,
    )
    with pytest.raises(InstagramBlocked) as caught:
        blocked.fetch_reels(["uno", "dos", "tres"], 20)
    assert calls == ["uno", "dos"]
    assert [item["shortCode"] for item in caught.value.partial] == ["A1"]
    assert as_instagram_block(TooManyRequestsException("please wait")) is not None
    assert as_instagram_block(Exception("checkpoint_required")) is not None

    skipped = []

    def lookup_soft(name):
        skipped.append(name)
        if name == "rota":
            raise ProfileMissing("no existe")
        return Profile([_post("C1", 5, 1, 5)])

    soft = InstaloaderSource(
        None,
        tmp_path / "no-se-usa.session",
        profile_lookup=lookup_soft,
        sleeper=lambda _delay: None,
        uniform=lambda low, _high: low,
    )
    saved = soft.fetch_reels(["rota", "sana"], 20)
    assert skipped == ["rota", "sana"]
    assert [item["shortCode"] for item in saved] == ["C1"]
    assert soft.warnings


def test_fallback_de_cuota_sin_reintentar_apify(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, APIFY_TOKEN="token-de-prueba")
    db = Database(settings.db_path)
    db.upsert_account("jordigpt", active=True)
    apify = FuenteFalsa(error=ApifyQuotaError("Apify respondió 402."))
    instaloader = FuenteFalsa(
        items=[
            {
                "id": "1",
                "shortCode": "ABC",
                "url": "https://www.instagram.com/reel/ABC/",
                "videoPlayCount": 10,
                "ownerUsername": "jordigpt",
                "timestamp": "2026-11-01T00:00:00+00:00",
                "caption": "[EJEMPLO] no es un reel real",
                "likesCount": 1,
                "commentsCount": 1,
                "videoUrl": "https://example.invalid/abc.mp4",
            }
        ]
    )
    outcome = execute_selected_run(
        db,
        settings,
        client=apify,
        instaloader_source=instaloader,
        yes=True,
        skip_transcribe=True,
        skip_llm=True,
    )
    assert apify.calls and len(apify.calls) == 1
    assert len(instaloader.calls) == 1
    assert outcome.source == "instaloader"
    assert outcome.status == "succeeded"
    assert "No se reintenta" in outcome.message


def test_sin_token_no_construye_apify_y_el_presupuesto_sigue_parando_apify(tmp_path, monkeypatch):
    sin_token = make_settings(monkeypatch, tmp_path)
    db = Database(sin_token.db_path)
    db.upsert_account("jordigpt", active=True)

    def prohibido(*_args, **_kwargs):
        raise AssertionError("no hay que construir el cliente de Apify")

    monkeypatch.setattr("radar.pipeline.ApifyClient", prohibido)
    instaloader = FuenteFalsa(
        items=[
            {
                "id": "1",
                "shortCode": "ZZ",
                "url": "https://www.instagram.com/reel/ZZ/",
                "videoPlayCount": 4,
                "ownerUsername": "jordigpt",
                "timestamp": "2026-11-01T00:00:00+00:00",
                "caption": "[EJEMPLO]",
            }
        ]
    )
    outcome = execute_selected_run(
        db,
        sin_token,
        instaloader_source=instaloader,
        yes=True,
        skip_transcribe=True,
        skip_llm=True,
    )
    assert outcome.source == "instaloader"
    assert outcome.estimate.estimated_usd == Decimal("0.00")

    with pytest.raises(MissingApifyToken):
        execute_selected_run(
            db,
            sin_token,
            requested_source="apify",
            instaloader_source=instaloader,
            yes=True,
            skip_transcribe=True,
            skip_llm=True,
        )

    con_token = make_settings(monkeypatch, tmp_path, APIFY_TOKEN="token-de-prueba")
    apify = FuenteFalsa()
    with pytest.raises(OverBudget):
        execute_selected_run(
            db,
            con_token,
            client=apify,
            instaloader_source=instaloader,
            requested_source="apify",
            reels_per_account=100_000,
            yes=True,
            skip_transcribe=True,
            skip_llm=True,
        )
    assert apify.calls == []


def test_login_no_recibe_ni_guarda_la_contrasena(tmp_path):
    assert "password" not in inspect.signature(login_secondary_account).parameters
    saved = {}

    class Loader:
        def __init__(self, **kwargs):
            saved["init"] = kwargs

        def interactive_login(self, username):
            saved["user"] = username

        def save_session_to_file(self, filename):
            saved["file"] = filename
            path = __import__("pathlib").Path(filename)
            path.write_text("session-blob", encoding="utf-8")

    class Module:
        Instaloader = Loader

    destination = tmp_path / "ig.session"
    login_secondary_account("secundaria", destination, module=Module)
    assert saved["user"] == "secundaria"
    assert "password" not in saved["init"]
    assert saved["init"]["max_connection_attempts"] == 1
    assert destination.stat().st_mode & 0o077 == 0
    assert "session-blob" not in saved["user"]


def test_apify_402_es_error_de_cuota():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(402, json={"error": "insufficient credits"})
        return httpx.Response(404)

    client = ApifyClient("test-token", transport=httpx.MockTransport(handler))
    with pytest.raises(ApifyQuotaError):
        client.fetch_reels(["jordigpt"], 20)
    client.close()
