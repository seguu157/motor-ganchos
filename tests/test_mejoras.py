"""Biblioteca, flujo, navegador y carruseles. Sin red y sin OCR real."""

from decimal import Decimal
from pathlib import Path

import pytest

from radar.apify_parse import parse_item
from radar.carrusel import completar_diapositivas, plantilla_carrusel, url_de_imagen_permitida
from radar.db import Database
from radar.dossier import construir_dossier
from radar.export_json import filter_entries
from radar.flujo import (
    FlujoError,
    aplicar_guion,
    aplicar_hook,
    aplicar_oferta,
    aplicar_referencias,
    aplicar_revision,
    aplicar_variantes,
    crear_proyecto,
    exportar_proyecto,
)
from radar.outliers import engagement_score
from radar.perfil import Perfil
from radar.pipeline import describe_plan, execute_selected_run, ingest_reels
from radar.sources.playwright_source import (
    BrowserBlocked,
    PageHit,
    PlaywrightSource,
    classify_page,
    login_browser,
    nodos_desde_json,
)
from radar.sources.select import block_reason, plan_source
from radar.web import create_app
from tests.conftest import make_settings


def test_interaccion_no_inventa_un_cero_y_pesa_el_comentario():
    assert engagement_score(10, 2, weight=4) == 18
    assert engagement_score(10, None) is None
    assert engagement_score(None, 4) is None


def test_el_carrusel_no_entra_en_la_mediana_de_reproducciones(tmp_path):
    reels = [
        parse_item(
            {
                "id": f"R{indice}",
                "shortCode": f"R{indice}",
                "url": f"https://www.instagram.com/reel/R{indice}/",
                "ownerUsername": "marca",
                "timestamp": f"2026-11-0{indice}T00:00:00+00:00",
                "videoPlayCount": cifra,
                "likesCount": 1,
                "commentsCount": 1,
            }
        )
        for indice, cifra in enumerate((100, 100, 100), start=1)
    ]
    bajo = parse_item(
        {
            "id": "C0",
            "shortCode": "C0",
            "ownerUsername": "marca",
            "timestamp": "2026-11-09T00:00:00+00:00",
            "productType": "carousel_container",
            "likesCount": 0,
            "commentsCount": 0,
            "slides": [{"texto": "Bajo"}],
        }
    )
    carrusel = parse_item(
        {
            "id": "C1",
            "shortCode": "C1",
            "ownerUsername": "marca",
            "timestamp": "2026-11-10T00:00:00+00:00",
            "productType": "carousel_container",
            "likesCount": 10,
            "commentsCount": 10,
            "videoPlayCount": 99999,
            "slides": [
                {"texto": "Primera diapositiva del ejemplo"},
                {"texto": "Segunda"},
                {"texto": "Cierre del ejemplo"},
            ],
        }
    )
    assert carrusel.media_kind == "carrusel"
    assert carrusel.video_play_count is None
    assert carrusel.url.endswith("/p/C1/")
    assert [slide["texto"] for slide in carrusel.slides][0].startswith("Primera")

    db = Database(tmp_path / "radar.sqlite")
    ingest_reels(db, [*reels, bajo, carrusel], is_example=False, run_id=None)
    filas = {row["short_code"]: row for row in db.list_reels()}
    assert filas["R1"]["is_outlier"] == 0
    assert filas["C1"]["is_outlier"] == 1
    assert filas["C1"]["engagement_score"] == 50
    assert filas["C1"]["video_play_count"] is None
    assert "Diapositiva 1 (gancho): [X]" in plantilla_carrusel(list(carrusel.slides))
    assert "99999" not in plantilla_carrusel(list(carrusel.slides))


def test_ocr_borra_la_imagen_salvo_que_se_pida_conservarla(tmp_path):
    def bajar(url, workdir, indice):
        destino = workdir / f"{indice}.jpg"
        destino.write_bytes(b"bytes-de-prueba")
        return destino

    def ocr(path):
        assert path.is_file()
        return "Texto leído en la diapositiva"

    slides = [{"indice": 1, "texto": "", "origen": "vacio", "imagen": "https://cdninstagram.com/a.jpg"}]
    trabajo = tmp_path / "trabajo"
    nuevos, quedaron = completar_diapositivas(
        slides,
        ocr=ocr,
        downloader=bajar,
        workdir=trabajo,
        conservar=False,
    )
    assert nuevos[0]["texto"] == "Texto leído en la diapositiva"
    assert nuevos[0]["origen"] == "ocr"
    assert quedaron == []
    assert list(trabajo.glob("*")) == []

    nuevos, quedaron = completar_diapositivas(
        slides,
        ocr=ocr,
        downloader=bajar,
        workdir=tmp_path / "conservar",
        conservar=True,
    )
    assert len(quedaron) == 1
    assert quedaron[0].is_file()


def test_no_descarga_imagenes_de_otro_host(tmp_path):
    assert url_de_imagen_permitida("https://cdninstagram.com/foto.jpg")
    assert not url_de_imagen_permitida("https://example.invalid/foto.jpg")
    assert not url_de_imagen_permitida("http://cdninstagram.com/foto.jpg")
    llamadas = []

    def bajar(url, workdir, indice):
        llamadas.append(url)
        return None

    nuevos, quedaron = completar_diapositivas(
        [{"indice": 1, "texto": "", "imagen": "https://example.invalid/foto.jpg"}],
        ocr=lambda _path: "no debería",
        downloader=bajar,
        workdir=tmp_path / "nada",
        conservar=False,
    )
    assert llamadas == []
    assert nuevos[0]["texto"] == ""
    assert quedaron == []


def test_el_navegador_pausa_y_para_sin_reintentar(tmp_path):
    sleeps = []
    spans = []

    def uniform(low, high):
        spans.append((low, high))
        return low

    class Navegador:
        def __init__(self):
            self.calls = []

        def open_page(self, username, kind):
            self.calls.append((username, kind))
            if username == "dos":
                return PageHit(status=429, url=f"https://www.instagram.com/{username}/reels/", text="")
            return PageHit(
                status=200,
                url=f"https://www.instagram.com/{username}/",
                text="perfil público",
                items=[
                    {
                        "shortcode": "AAA",
                        "product_type": "clips",
                        "like_count": 3,
                        "comment_count": 1,
                        "video_play_count": 20,
                        "taken_at": 1_763_424_000,
                        "owner": {"username": username},
                        "edge_media_to_caption": {"edges": [{"node": {"text": "pie de ejemplo"}}]},
                    }
                ],
            )

        def close(self):
            pass

    navegador = Navegador()
    source = PlaywrightSource(
        tmp_path / "perfil",
        max_accounts=2,
        max_posts=5,
        navigator=navegador,
        sleeper=lambda delay: sleeps.append(delay),
        uniform=uniform,
        account_delay=(25, 70),
        post_delay=(3, 8),
    )
    with pytest.raises(BrowserBlocked) as exc:
        source.fetch_reels(["uno", "dos", "tres"], 20)
    assert exc.value.partial[0]["shortCode"] == "AAA"
    assert exc.value.partial[0]["videoPlayCount"] == 20
    assert navegador.calls == [("uno", "reels"), ("uno", "posts"), ("dos", "reels")]
    assert sleeps == [3, 25]
    assert spans[0] == (3, 8)
    assert "429" in str(exc.value)
    assert "no reintento" in str(exc.value)
    assert source.skipped == ["tres"]


def test_clasifica_bloqueos_y_no_se_queda_con_otra_cuenta():
    assert classify_page(status=401, url="https://www.instagram.com/a/", text="") == "401"
    assert classify_page(status=429, url="", text="") == "429"
    assert classify_page(status=200, url="https://www.instagram.com/challenge/x", text="") == "challenge"
    assert classify_page(status=200, url="https://www.instagram.com/checkpoint/", text="") == "checkpoint"
    assert classify_page(status=200, url="https://www.instagram.com/accounts/login/", text="") == "login"
    assert classify_page(status=200, url="https://www.instagram.com/a/", text="Esta cuenta es privada") == "privada"
    assert classify_page(status=200, url="https://www.instagram.com/a/", text="hola") is None

    nodo = {
        "shortcode": "BBB",
        "like_count": 4,
        "comment_count": 2,
        "owner": {"username": "otra"},
        "product_type": "clips",
    }
    assert nodos_desde_json({"data": nodo}, "vigilada") == []
    propio = nodos_desde_json({"data": {**nodo, "owner": {"username": "vigilada"}}}, "vigilada")
    assert propio[0]["ownerUsername"] == "vigilada"


def test_login_no_recibe_ni_escribe_la_contrasena(tmp_path):
    import inspect

    assert "password" not in inspect.signature(login_browser).parameters

    def starter(profile_dir: Path) -> None:
        cookies = profile_dir / "Default" / "Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.write_bytes(b"cookie-de-prueba")

    destino = tmp_path / "ig-browser"
    login_browser(destino, username="secundaria", starter=starter)
    marker = (destino / "perfil.listo").read_text(encoding="utf-8")
    assert marker.strip() == "secundaria"
    assert "cookie-de-prueba" not in marker
    assert "password" not in marker.lower()


def test_la_fuente_por_defecto_es_el_navegador(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, APIFY_TOKEN="token-de-prueba")
    assert settings.radar_source == "playwright"
    plan = plan_source(settings, ["jordigpt", "dani_llamazares"], 20)
    assert plan.effective == "playwright"
    assert plan.estimate.estimated_usd == Decimal("0.00")
    texto = describe_plan(plan)
    assert "0,00 USD" in texto
    assert "condiciones de Instagram" in texto
    motivo = block_reason(settings, plan, allow_over_budget=False)
    assert motivo.startswith("Falta la sesión")
    assert "No se ha hecho ninguna petición" in motivo


def test_dossier_usa_tiempos_reales_y_huecos(tmp_path):
    from radar.seed import seed_examples

    db = Database(tmp_path / "radar.sqlite")
    seed_examples(db)
    entrada = next(item for item in db.list_bank() if item.account == "jordigpt")
    dossier = construir_dossier(entrada, Perfil(oferta="", cta=""))
    assert dossier.gancho.startswith("¿Sigues")
    assert dossier.receta
    assert dossier.prompts and "[X]" in dossier.prompts[0]
    assert dossier.ajustes
    assert dossier.ideas
    assert "no un resultado" in dossier.monetizacion
    assert "[X]" in dossier.adaptacion
    assert dossier.desglose
    assert "0,0–2,8 s" in dossier.desglose[0]
    assert "No se inventa" not in dossier.desglose_nota


def test_el_flujo_rechaza_ejemplos_y_exporta_la_plantilla(tmp_path):
    from radar.seed import seed_examples

    db = Database(tmp_path / "radar.sqlite")
    reel = parse_item(
        {
            "id": "REAL1",
            "shortCode": "REAL1",
            "url": "https://www.instagram.com/reel/REAL1/",
            "ownerUsername": "cuenta",
            "timestamp": "2026-11-02T00:00:00+00:00",
            "videoPlayCount": 40,
            "likesCount": 5,
            "commentsCount": 1,
            "caption": "pie sin cifras inventadas",
        }
    )
    base = parse_item(
        {
            "id": "REAL0",
            "shortCode": "REAL0",
            "url": "https://www.instagram.com/reel/REAL0/",
            "ownerUsername": "cuenta",
            "timestamp": "2026-11-01T00:00:00+00:00",
            "videoPlayCount": 0,
            "likesCount": 1,
            "commentsCount": 0,
        }
    )
    ingest_reels(db, [base, reel], is_example=False, run_id=None)
    fila = next(row for row in db.list_reels() if row["short_code"] == "REAL1")
    assert fila["is_outlier"] == 1
    db.save_transcript(
        fila["id"],
        text="Gancho real de la pieza.",
        language="es",
        hook_text="Gancho real",
        segments=[],
        model="test",
    )
    db.save_analysis(
        fila["id"],
        hook_type="pregunta",
        blocks=[{"id": "gancho", "texto": "¿[X]?", "en_pantalla": "[X]"}],
        template="Gancho: ¿[X]?\nCierre: prueba [X].",
        adapted_script="¿[X]?\nCTA: [X]",
        warnings=[],
        provider="test",
        model="test",
        raw=None,
    )
    seed_examples(db)
    ejemplo_id = next(item.reel_id for item in db.list_bank() if item.is_example)
    real_id = next(item.reel_id for item in db.list_bank() if item.short_code == "REAL1")

    proyecto = crear_proyecto(db, "Pieza de prueba")
    aplicar_oferta(db, proyecto.id, Perfil(oferta="Acompañamiento para [X]", cta="Escríbeme"))
    with pytest.raises(FlujoError, match="ejemplo"):
        aplicar_referencias(db, proyecto.id, [ejemplo_id])
    aplicar_referencias(db, proyecto.id, [real_id])
    aplicar_hook(db, proyecto.id, real_id)
    aplicar_guion(db, proyecto.id)
    aplicar_variantes(db, proyecto.id)
    aplicar_revision(db, proyecto.id, "Revisado. Sin cifras nuevas.", aprobar=True)
    texto = exportar_proyecto(db, proyecto.id)
    assert "Escríbeme" in texto
    assert "[X]" in texto
    assert "**Ejemplo:** no" in texto


def test_la_biblioteca_filtra_por_categoria_y_acentos(tmp_path):
    from radar.seed import seed_examples

    db = Database(tmp_path / "radar.sqlite")
    seed_examples(db)
    pregunta = filter_entries(db.list_bank(), category="pregunta de apertura")
    assert pregunta and all(item.categoria == "pregunta de apertura" for item in pregunta)
    assert filter_entries(db.list_bank(), query="pestañas")
    assert filter_entries(db.list_bank(), query="pestanas")
    assert filter_entries(db.list_bank(), media_kind="carrusel") == []


def test_la_web_muestra_la_ficha_el_flujo_y_el_navegador(settings):
    import re

    from fastapi.testclient import TestClient

    client = TestClient(create_app(settings))
    banco = client.get("/banco?q=pestanas&categoria=pregunta+de+apertura")
    assert banco.status_code == 200
    assert "pregunta de apertura" in banco.text
    match = re.search(r'href="/banco/(\d+)"', client.get("/banco").text)
    ficha = client.get(f"/banco/{match.group(1)}")
    assert ficha.status_code == 200
    for titulo in (
        "Gancho",
        "Receta",
        "Prompts",
        "Ajustes de edición",
        "Ideas derivadas",
        "Monetización",
        "Adaptación",
        "Desglose",
    ):
        assert titulo in ficha.text
    assert "[X]" in ficha.text

    flujo = client.get("/flujo")
    assert flujo.status_code == 200
    assert "Oferta" in flujo.text
    creado = client.post("/flujo", data={"titulo": "Pieza web"}, follow_redirects=False)
    assert creado.status_code == 303
    vacio = client.post("/flujo", data={"titulo": "  "})
    assert vacio.status_code == 400

    perfil = client.post(
        "/perfil",
        data={"oferta": "Oferta de prueba", "cta": "Escríbeme"},
        follow_redirects=True,
    )
    assert perfil.status_code == 200
    assert "Oferta de prueba" in perfil.text

    radar = client.get("/radar")
    assert "0,26" in radar.text
    assert "2,60" in radar.text
    assert "Navegador" in radar.text


def test_run_con_navegador_no_llama_a_apify(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, APIFY_TOKEN="token-de-prueba")
    destino = settings.browser_profile
    cookies = destino / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"x")
    (destino / "perfil.listo").write_text("secundaria\n", encoding="utf-8")
    settings = make_settings(monkeypatch, tmp_path, APIFY_TOKEN="token-de-prueba")
    db = Database(settings.db_path)
    db.upsert_account("jordigpt", active=True)

    class Navegador:
        def open_page(self, username, kind):
            return PageHit(
                status=200,
                url=f"https://www.instagram.com/{username}/",
                items=[
                    {
                        "shortcode": "NAV1",
                        "product_type": "clips",
                        "like_count": 2,
                        "comment_count": 1,
                        "video_play_count": 8,
                        "owner": {"username": username},
                    }
                ],
            )

        def close(self):
            pass

    def prohibido(*_args, **_kwargs):
        raise AssertionError("no hay que construir el cliente de Apify")

    monkeypatch.setattr("radar.pipeline.ApifyClient", prohibido)
    source = PlaywrightSource(
        settings.browser_profile,
        navigator=Navegador(),
        sleeper=lambda _delay: None,
        uniform=lambda low, _high: low,
    )
    outcome = execute_selected_run(
        db,
        settings,
        browser_source=source,
        yes=True,
        skip_transcribe=True,
        skip_llm=True,
    )
    assert outcome.source == "playwright"
    assert outcome.status == "succeeded"
    assert outcome.reels_saved == 1
