"""Radar de creadores de Instagram. No abre el navegador: la sesión es falsa."""

from __future__ import annotations

import csv
import random
from pathlib import Path

import httpx
import pytest

from radar.cli import main
from radar.instagram.apify_opcional import buscar_handles, handles_de_items
from radar.instagram.clasificar import Clasificador, heuristica
from radar.instagram.csvio import exportar_csv, importar_csv
from radar.instagram.descubrir import ejecutar_actualizacion, ejecutar_descubrimiento
from radar.instagram.fuente import InstagramBloqueado, codigo_de_pagina, mensaje_parada, url_de_semilla
from radar.instagram.parse import handles_en_html, parsear_blobs
from radar.instagram.programar import instalar_tarea, preparar_tarea
from radar.instagram.reglas import Reel, entero_vistas, evaluar, normalizar_handle
from radar.instagram.store import COLUMNAS_CSV, anadir_semilla, guardar_cuenta, listar_cuentas, listar_semillas, obtener_cuenta
from radar.web import create_app
from tests.conftest import fresh_db

from fastapi.testclient import TestClient


def _reel(code: str, plays, *, fijado: bool = False, taken: str | None = "2026-11-01", caption: str = "herramientas de IA") -> Reel:
    return Reel(
        code=code,
        url=f"https://www.instagram.com/reel/{code}/",
        plays=plays,
        caption=caption,
        taken_at=taken,
        fijado=fijado,
    )


def _lectura_viral(
    handle: str = "creador.ia",
    *,
    bio: str = "Herramientas de IA para automatizar",
    plays_top: int = 80_000,
    caption: str = "herramientas de IA @mencion.ia",
    nombre: str = "Ada IA",
):
    items = []
    for indice in range(5):
        items.append(
            {
                "code": f"R{indice:02d}xxxx",
                "product_type": "clips",
                "play_count": 10_000,
                "caption": {"text": "reel normal"},
                "taken_at": 1_763_000_000 + indice * 86_400,
                "user": {"username": handle},
            }
        )
    items.append(
        {
            "code": "VIRAL1",
            "product_type": "clips",
            "play_count": plays_top,
            "caption": {"text": caption},
            "taken_at": 1_764_000_000,
            "user": {"username": handle},
            "coauthor_producers": [{"username": "colab.ia"}],
            "usertags": {"in": [{"user": {"username": "etiquetada.ia"}}]},
        }
    )
    return parsear_blobs(
        [
            {
                "user": {
                    "username": handle,
                    "full_name": nombre,
                    "biography": bio,
                    "external_url": "https://ada.example",
                    "follower_count": 1500,
                    "is_verified": False,
                    "edge_related_profiles": {"edges": [{"node": {"username": "sugerida.ia"}}, {"node": {"username": "yo.cuenta"}}]},
                },
                "viewer": {"username": "yo.cuenta"},
                "items": items,
            }
        ],
        handle,
    )


class _Sesion:
    def __init__(self) -> None:
        self.perfiles: dict = {}
        self.paginas: dict = {}
        self.leidos: list[str] = []
        self.paginas_abiertas: list[str] = []
        self.bloqueo: set[str] = set()

    def leer_perfil(self, handle: str):
        self.leidos.append(handle)
        if handle in self.bloqueo:
            raise InstagramBloqueado(mensaje_parada("challenge"))
        return self.perfiles[handle]

    def handles_de_pagina(self, url: str) -> list[str]:
        self.paginas_abiertas.append(url)
        if url in self.bloqueo:
            raise InstagramBloqueado(mensaje_parada("429"))
        return list(self.paginas.get(url, []))

    def close(self) -> None:
        return None


def _kwargs(sesion: _Sesion):
    return {
        "sesion": sesion,
        "permitir_red": True,
        "sin_llm": True,
        "rng": random.Random(0),
        "dormir": lambda _segundos: None,
        "pausa": (0, 0),
    }


def test_la_mediana_usa_hasta_20_recientes_y_exige_suelo():
    reels = [_reel("OLD", 9_000_000, taken="2020-01-01")]
    for indice in range(19):
        reels.append(_reel(f"S{indice:02d}", 10_000, taken=f"2026-11-{indice + 1:02d}"))
    reels.append(_reel("BIG", 80_000, taken="2026-11-20"))
    reels.append(_reel("PIN", 2_000_000, fijado=True, taken="2024-01-01"))
    reels.append(_reel("SIN", None, taken="2026-11-21"))
    resultado = evaluar(reels, bio="Herramientas de IA", handle="creador.ia")
    assert resultado.reels_muestreados == 20
    assert resultado.mediana_vistas == 10_000
    assert resultado.tiene_viral is True
    assert resultado.num_piezas_virales == 2
    assert resultado.num_virales_no_fijadas == 1
    assert resultado.mejor_pieza_vistas == 2_000_000
    assert resultado.mejor_pieza_fijada == "sí"
    assert "OLD" not in resultado.piezas_virales
    assert "SIN" not in resultado.piezas_virales
    bajo = evaluar(
        [_reel(str(indice), 40_000 if indice == 0 else 10_000, taken=f"2026-01-{indice + 1:02d}") for indice in range(10)],
        bio="Herramientas de IA",
        handle="baja",
    )
    assert bajo.tiene_viral is False
    assert "alcance bajo" in bajo.motivo
    assert entero_vistas("1.2M") is None
    assert entero_vistas(None) is None
    assert entero_vistas("1200") == 1200
    assert normalizar_handle("https://www.instagram.com/Creador.IA/reels/") == "creador.ia"
    assert normalizar_handle("https://www.instagram.com/reel/ABC123/") == ""


def test_parsea_la_ficha_sin_inventar_reproducciones_ni_meter_al_viewer():
    lectura = parsear_blobs(
        [
            {
                "user": {
                    "username": "creador.ia",
                    "full_name": "Ada",
                    "biography": "Herramientas de IA\npara negocios",
                    "external_url": "https://ada.example",
                    "edge_followed_by": {"count": 1200},
                    "is_verified": True,
                    "edge_related_profiles": {"edges": [{"node": {"username": "sugerida.ia"}}]},
                },
                "viewer": {"username": "yo.cuenta"},
                "items": [
                    {
                        "code": "AAA111",
                        "product_type": "clips",
                        "media_type": 2,
                        "play_count": 80_000,
                        "caption": {"text": "automatiza con IA @mencion.ia"},
                        "taken_at": 1_764_000_000,
                        "clips_tab_pinned_user_ids": [1],
                        "user": {"username": "creador.ia"},
                        "coauthor_producers": [{"username": "colab.ia"}],
                    },
                    {
                        "code": "BBB222",
                        "product_type": "clips",
                        "play_count": "1.2M",
                        "user": {"username": "creador.ia"},
                    },
                    {"code": "CCC333", "media_type": 8, "play_count": 999, "user": {"username": "creador.ia"}},
                    {"code": "DDD444", "product_type": "feed", "media_type": 2, "play_count": 50, "user": {"username": "creador.ia"}},
                    {"code": "EEE555", "product_type": "clips", "play_count": 10, "user": {"username": "otra.persona"}},
                ],
            }
        ],
        "Creador.IA",
    )
    assert lectura.seguidores == 1200
    assert lectura.verificado is True
    assert "\n" in lectura.bio
    assert lectura.sugeridos == ["sugerida.ia"]
    assert "yo.cuenta" not in lectura.sugeridos
    codes = {reel.code: reel for reel in lectura.reels}
    assert set(codes) == {"AAA111", "BBB222"}
    assert codes["AAA111"].plays == 80_000
    assert codes["AAA111"].fijado is True
    assert codes["BBB222"].plays is None
    assert lectura.colaboradores == ["colab.ia"]
    assert handles_en_html(
        '<a href="https://www.instagram.com/creador.ia/">x</a> <a href="https://www.instagram.com/reel/ABC/">r</a>'
    ) == ["creador.ia"]
    assert codigo_de_pagina(status=200, url="https://www.instagram.com/challenge/1", text="ok", statuses=[]) == "challenge"
    assert codigo_de_pagina(status=200, url="https://www.instagram.com/x/reels/", text="ok", statuses=[429]) == "429"
    assert codigo_de_pagina(status=403, url="https://www.instagram.com/x/reels/", text="ok", statuses=[]) == "403"
    assert codigo_de_pagina(status=200, url="https://www.instagram.com/x/reels/", text="ok", statuses=[403]) is None
    assert codigo_de_pagina(status=200, url="https://www.instagram.com/x/", text="This account is private", statuses=[]) == "privada"
    assert url_de_semilla("keyword", "ganar dinero con ia") == "https://www.instagram.com/explore/tags/ganardineroconia/"
    assert url_de_semilla("hashtag", "#AITools") == "https://www.instagram.com/explore/tags/aitools/"


def test_el_corte_se_importa_sin_duplicar_y_no_rellena_huecos(tmp_path):
    db = fresh_db(tmp_path)
    origen = Path(__file__).resolve().parents[1] / "radar" / "fixtures" / "cuentas-instagram.csv"
    total = importar_csv(db, origen)
    assert total == 34
    assert importar_csv(db, origen) == 34
    assert len(listar_cuentas(db, estado="califica")) == 34
    cuenta = obtener_cuenta(db, "sanmunoz.ia")
    assert cuenta is not None
    assert cuenta["subnicho"] == "Agentes IA y Claude Code"
    assert cuenta["idioma"] == "es"
    assert cuenta["seguidores"] == 184_501
    assert cuenta["mejor_pieza_vistas"] == 1_382_086
    assert cuenta["mejor_pieza_multiplo"] == pytest.approx(50.6)
    assert cuenta["fecha_detectada"] == "2026-11-24"
    assert cuenta["verificado"] == "sí"
    with db.connect() as conn:
        conn.execute(
            "UPDATE ig_creadores SET seguidores = 1, fecha_detectada = '2020-01-01' WHERE handle = 'sanmunoz.ia'"
        )
    importar_csv(db, origen)
    cuenta = obtener_cuenta(db, "sanmunoz.ia")
    assert cuenta["seguidores"] == 184_501
    assert cuenta["fecha_detectada"] == "2020-01-01"
    vacio = tmp_path / "vacio.csv"
    vacio.write_text(",".join(COLUMNAS_CSV) + "\nvacio.ia," + "," * (len(COLUMNAS_CSV) - 2) + "\n", encoding="utf-8")
    importar_csv(db, vacio)
    hueco = obtener_cuenta(db, "vacio.ia")
    assert hueco is not None
    assert hueco["seguidores"] is None
    assert hueco["mejor_pieza_vistas"] is None
    assert hueco["verificado"] == ""
    assert hueco["mediana_vistas"] is None
    texto = exportar_csv(db, estado="califica")
    filas = list(csv.DictReader(texto.splitlines()))
    assert list(filas[0].keys()) == list(COLUMNAS_CSV)
    assert {fila["handle"] for fila in filas} == {cuenta["handle"] for cuenta in listar_cuentas(db)}
    assert exportar_csv(db, estado="califica") == texto


def test_descubre_por_lista_mencion_y_sugerida_y_para_si_hay_challenge(settings, tmp_path):
    db = fresh_db(tmp_path)
    sesion = _Sesion()
    sesion.perfiles["creador.ia"] = _lectura_viral()
    sesion.bloqueo.add("segunda.ia")
    sesion.paginas["https://listas.test/creadores"] = ["creador.ia", "segunda.ia", "reel"]
    anadir_semilla(db, "url", "https://listas.test/creadores")
    dormidos: list[float] = []
    opciones = _kwargs(sesion)
    opciones["dormir"] = dormidos.append
    opciones["pausa"] = (25, 50)
    opciones["rng"] = random.Random(1)
    informe = ejecutar_descubrimiento(db, settings, max_cuentas=2, max_paginas=1, **opciones)
    assert informe.bloqueado is True
    assert "challenge" in informe.mensaje
    assert sesion.leidos == ["creador.ia", "segunda.ia"]
    assert dormidos
    assert all(25 <= pausa <= 50 for pausa in dormidos)
    cuenta = obtener_cuenta(db, "creador.ia")
    assert cuenta is not None
    assert cuenta["estado"] == "califica"
    assert cuenta["seguidores"] == 1500
    assert cuenta["mediana_vistas"] == 10_000
    assert cuenta["mejor_pieza_vistas"] == 80_000
    assert cuenta["nicho"] == "IA"
    assert cuenta["subnicho"] == "Automatización"
    assert cuenta["idioma"] == "es"
    assert cuenta["verificado"] == "no"
    assert "REVISAR" in cuenta["clasificacion"]
    assert "pestaña Reels" in cuenta["fuente"]
    assert obtener_cuenta(db, "mencion.ia")["estado"] == "candidata"
    assert obtener_cuenta(db, "sugerida.ia")["semilla"].startswith("sugerida:")
    assert obtener_cuenta(db, "colab.ia")["semilla"].startswith("colab:")
    assert obtener_cuenta(db, "etiquetada.ia")["semilla"].startswith("colab:")
    assert obtener_cuenta(db, "yo.cuenta") is None
    assert obtener_cuenta(db, "reel") is None
    segunda = obtener_cuenta(db, "segunda.ia")
    assert segunda["estado"] == "candidata"
    assert segunda["seguidores"] is None
    assert segunda["mediana_vistas"] is None


def test_un_viral_fuera_de_nicho_no_entra_ni_arrastra_menciones(settings, tmp_path):
    db = fresh_db(tmp_path)
    sesion = _Sesion()
    sesion.perfiles["cocina.rica"] = _lectura_viral(
        "cocina.rica",
        bio="Recetas de cocina para el finde",
        caption="Receta del finde @marca.sarten",
        nombre="Ada Cocina",
    )
    anadir_semilla(db, "handle", "cocina.rica")
    informe = ejecutar_descubrimiento(db, settings, max_cuentas=1, max_paginas=0, **_kwargs(sesion))
    assert informe.bloqueado is False
    cuenta = obtener_cuenta(db, "cocina.rica")
    assert cuenta["estado"] == "descartada"
    assert cuenta["motivo"] == "fuera de nicho"
    assert cuenta["nicho"] == ""
    assert obtener_cuenta(db, "marca.sarten") is None
    assert heuristica(
        handle="cocina.rica", nombre="", bio="Recetas de cocina", enlace="", pies="", idioma=""
    ).nicho == "fuera"


def test_actualizar_conserva_la_revision_manual_y_no_borra_si_falla(settings, tmp_path):
    db = fresh_db(tmp_path)
    guardar_cuenta(
        db,
        {
            "handle": "creador.ia",
            "estado": "califica",
            "nicho": "IA",
            "subnicho": "Herramientas IA",
            "idioma": "es",
            "clasificacion": "revisada a mano (bio)",
            "fecha_detectada": "2026-11-24",
            "fecha_actualizada": "2026-11-24",
            "seguidores": 10,
            "mediana_vistas": 5,
            "mejor_pieza_vistas": 90_000,
        },
    )

    class _Bomba:
        def clasificar(self, **_kwargs):
            raise AssertionError("no tenía que clasificar una ficha revisada a mano")

        def close(self) -> None:
            return None

    sesion = _Sesion()
    sesion.perfiles["creador.ia"] = _lectura_viral(plays_top=120_000)
    informe = ejecutar_actualizacion(
        db,
        settings,
        handle="creador.ia",
        clasificador=_Bomba(),
        **_kwargs(sesion),
    )
    assert informe.bloqueado is False
    cuenta = obtener_cuenta(db, "creador.ia")
    assert cuenta["clasificacion"] == "revisada a mano (bio)"
    assert cuenta["subnicho"] == "Herramientas IA"
    assert cuenta["fecha_detectada"] == "2026-11-24"
    assert cuenta["seguidores"] == 1500
    assert cuenta["mejor_pieza_vistas"] == 120_000
    from radar.instagram.descubrir import hoy

    assert cuenta["fecha_actualizada"] == hoy()

    sesion.perfiles["creador.ia"] = parsear_blobs([{"viewer": {"username": "yo.cuenta"}}], "creador.ia")
    sesion.perfiles["creador.ia"].error = "sin ficha"
    ejecutar_actualizacion(db, settings, handle="creador.ia", clasificador=_Bomba(), **_kwargs(sesion))
    cuenta = obtener_cuenta(db, "creador.ia")
    assert cuenta["estado"] == "califica"
    assert cuenta["seguidores"] == 1500
    assert cuenta["mediana_vistas"] == 10_000
    assert cuenta["mejor_pieza_vistas"] == 120_000


def test_el_llm_clasifica_y_si_falla_entra_la_heuristica():
    class _Modelo:
        def __init__(self, texto: str):
            self.texto = texto
            self.llamadas = 0

        def complete(self, system: str, user: str) -> str:
            self.llamadas += 1
            if self.texto == "rompe":
                raise ValueError("no es json")
            return self.texto

    modelo = _Modelo('{"nicho":"Negocio con IA","subnicho":"IA para solopreneurs","idioma":"en"}')
    etiqueta = Clasificador(modelo, "codex").clasificar(
        handle="justyn.ai",
        nombre="Justyn",
        bio="Helping solo founders save hours with AI",
        enlace="",
        pies="",
        idioma="en",
    )
    assert modelo.llamadas == 1
    assert etiqueta.nicho == "Negocio con IA"
    assert etiqueta.subnicho == "IA para solopreneurs"
    assert etiqueta.clasificacion == "llm (codex)"
    assert etiqueta.en_nicho is True
    roto = _Modelo("rompe")
    fallback = Clasificador(roto, "codex").clasificar(
        handle="creador.ia",
        nombre="",
        bio="Herramientas de IA para automatizar",
        enlace="",
        pies="",
        idioma="es",
    )
    assert fallback.clasificacion.startswith("heurística")
    assert fallback.nicho == "IA"
    assert fallback.subnicho == "Automatización"


def test_apify_sin_token_no_pide_nada_y_no_guarda_sus_vistas(monkeypatch, settings, tmp_path):
    original = httpx.Client

    def boom(*_args, **_kwargs):
        raise AssertionError("no tenía que abrirse un cliente HTTP")

    monkeypatch.setattr(httpx, "Client", boom)
    saltado = buscar_handles(None, "apify/instagram-scraper", ["ai tools"])
    assert saltado.llamado is False
    assert saltado.handles == []
    monkeypatch.setattr(httpx, "Client", original)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "secreto" not in str(request.url)
        assert request.headers["authorization"] == "Bearer secreto"
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"data": {"id": "r1", "status": "SUCCEEDED", "defaultDatasetId": "d1"}})
        return httpx.Response(200, json=[{"ownerUsername": "desde.apify", "videoPlayCount": 999999}])

    con_token = buscar_handles(
        "secreto",
        "apify/instagram-scraper",
        ["ai tools"],
        transport=httpx.MockTransport(handler),
    )
    assert con_token.handles == ["desde.apify"]
    assert handles_de_items([{"videoPlayCount": 5}]) == []

    def falso_apify(_token, _actor, _consultas):
        from radar.instagram.apify_opcional import ResultadoApify

        return ResultadoApify(handles=["desde.apify"], llamado=True, mensaje="solo usuarios")

    db = fresh_db(tmp_path)
    sesion = _Sesion()
    sesion.perfiles["desde.apify"] = _lectura_viral("desde.apify")
    monkeypatch.setenv("APIFY_TOKEN", "secreto")
    monkeypatch.setenv("RADAR_IG_CREADORES_APIFY_ACTOR", "apify/instagram-scraper")
    from radar.config import load_settings

    informe = ejecutar_descubrimiento(
        db,
        load_settings(),
        usar_apify=True,
        apify_buscar=falso_apify,
        max_cuentas=1,
        max_paginas=0,
        **_kwargs(sesion),
    )
    assert informe.bloqueado is False
    cuenta = obtener_cuenta(db, "desde.apify")
    assert cuenta["mejor_pieza_vistas"] == 80_000
    assert "999999" not in (cuenta.get("piezas_virales") or "")


def test_una_pagina_bloqueada_no_lee_perfiles(settings, tmp_path):
    db = fresh_db(tmp_path)
    sesion = _Sesion()
    sesion.paginas["https://listas.test/creadores"] = ["creador.ia"]
    sesion.bloqueo.add("https://listas.test/creadores")
    anadir_semilla(db, "url", "https://listas.test/creadores")
    informe = ejecutar_descubrimiento(db, settings, max_cuentas=2, max_paginas=1, **_kwargs(sesion))
    assert informe.bloqueado is True
    assert "429" in informe.mensaje
    assert sesion.leidos == []
    assert obtener_cuenta(db, "creador.ia") is None


def test_sin_yes_no_hay_peticion_y_la_tarea_no_llama_a_schtasks(settings, tmp_path, monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise AssertionError("no tenía que abrirse el navegador")

    monkeypatch.setattr("radar.instagram.fuente.PlaywrightSesion", boom)
    monkeypatch.setattr("subprocess.run", boom)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RADAR_DB", str(tmp_path / "radar.sqlite"))
    assert main(["creadores", "descubrir"]) == 0
    salida = capsys.readouterr().out
    assert "No se ha hecho ninguna petición" in salida
    assert settings.ig_creadores_max == 30
    assert settings.ig_creadores_pause == (25.0, 50.0)
    assert main(["creadores", "descubrir", "--yes"]) == 2
    error = capsys.readouterr().err
    assert "radar login" in error
    assert main(["creadores", "programar", "--hora", "07:30"]) == 0
    programado = capsys.readouterr().out
    assert "schtasks /Create" in programado
    assert "creadores ciclo --yes" in programado
    script = tmp_path / "data" / "radar-creadores-instagram.ps1"
    assert script.is_file()
    assert "ciclo --yes" in script.read_text(encoding="utf-8")
    import os

    monkeypatch.setattr(os, "name", "posix")
    programa = preparar_tarea(cwd=tmp_path, destino=tmp_path / "tarea.ps1", hora="07:30", python=r"C:\repo\.venv\Scripts\python.exe")
    assert "Windows" in instalar_tarea(programa)
    with pytest.raises(ValueError):
        preparar_tarea(hora="25:99")


def test_la_web_filtra_por_subnicho_idioma_seguidores_y_reel(settings):
    client = TestClient(create_app(settings))
    pagina = client.get("/instagram")
    assert pagina.status_code == 200
    assert "sanmunoz.ia" in pagina.text
    assert "Creadores de IA" in pagina.text
    sub = client.get("/instagram", params={"subnicho": "Agentes IA y Claude Code"})
    assert "sanmunoz.ia" in sub.text
    assert "theaiagents" not in sub.text
    ingles = client.get("/instagram", params={"idioma": "en"})
    assert "theaiagents" in ingles.text
    assert "sanmunoz.ia" not in ingles.text
    grandes = client.get("/instagram", params={"seguidores": "3000000"})
    assert "danmartell" in grandes.text
    assert "sanmunoz.ia" not in grandes.text
    vistas = client.get("/instagram", params={"vistas": "17000000"})
    assert "taki.gpt" in vistas.text
    assert "sanmunoz.ia" not in vistas.text
    multiplo = client.get("/instagram", params={"multiplo": "500"})
    assert "taki.gpt" in multiplo.text
    assert "sanmunoz.ia" not in multiplo.text
    ficha = client.get("/instagram/sanmunoz.ia")
    assert ficha.status_code == 200
    assert "Agentes IA y Claude Code" in ficha.text
    assert "revisada a mano" in ficha.text
    assert client.get("/instagram/no-existe").status_code == 404
    csv_resp = client.get("/instagram/exportar.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.text.splitlines()[0] == ",".join(COLUMNAS_CSV)
    alta = client.post("/instagram/semilla", data={"valor": "@semilla.nueva", "tipo": "handle"}, follow_redirects=False)
    assert alta.status_code == 303
    from radar.db import Database

    db = Database(settings.db_path)
    assert any(semilla["valor"] == "semilla.nueva" for semilla in listar_semillas(db))
