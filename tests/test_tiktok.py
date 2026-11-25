"""Radar de cuentas TikTok. No sale a la red: el transporte es falso."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from radar.cli import main
from radar.tiktok.apify_opcional import buscar_handles, handles_de_items
from radar.tiktok.clasificar import Clasificador, heuristica
from radar.tiktok.csvio import COLUMNAS_CSV, exportar_csv, importar_csv
from radar.tiktok.descubrir import ejecutar_actualizacion, ejecutar_descubrimiento
from radar.tiktok.fuente import Respuesta
from radar.tiktok.parse import parsear_embed, parsear_perfil
from radar.tiktok.programar import instalar_tarea, preparar_tarea
from radar.tiktok.reglas import entero_vistas, evaluar, normalizar_handle
from radar.tiktok.reglas import Pieza
from radar.tiktok.store import anadir_semilla, guardar_cuenta, listar_cuentas, obtener_cuenta
from radar.web import create_app
from tests.conftest import fresh_db

from fastapi.testclient import TestClient


class _Red:
    def __init__(self, paginas: dict[str, Respuesta] | None = None, estado: int = 200, texto: str = "<html>vacio</html>"):
        self.paginas = paginas or {}
        self.estado = estado
        self.texto = texto
        self.urls: list[str] = []

    def get(self, url: str) -> Respuesta:
        self.urls.append(url)
        if url in self.paginas:
            return self.paginas[url]
        return Respuesta(self.estado, self.texto, url)

    def close(self) -> None:
        return None


def _id(segundos: int, n: int = 1) -> str:
    return str((segundos << 32) + n)


def _perfil(handle: str, *, bio: str, enlace: str = "", seguidores: int | None = 1200, idioma: str = "en") -> str:
    stats = {} if seguidores is None else {"followerCount": seguidores, "videoCount": 12}
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "statusCode": 0,
                "userInfo": {
                    "user": {
                        "uniqueId": handle,
                        "nickname": handle,
                        "signature": bio,
                        "bioLink": {"link": enlace} if enlace else {},
                        "language": idioma,
                        "privateAccount": False,
                    },
                    "statsV2": stats,
                },
            }
        }
    }
    return f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(payload)}</script>'


def _embed(handle: str, piezas: list[dict]) -> str:
    payload = {"source": {"data": {f"/embed/@{handle}": {"videoList": piezas}}}}
    return f'<script id="__FRONTITY_CONNECT_STATE__" type="application/json">{json.dumps(payload)}</script>'


def _pieza(indice: int, plays, *, slideshow: bool = True, desc: str = "tips") -> dict:
    item = {
        "id": _id(1_700_000_000 + indice, indice + 1),
        "desc": desc,
        "coverUrl": "https://p16.tiktokcdn.com/obj/photomode/abc" if slideshow else "https://p16.tiktokcdn.com/video/abc",
    }
    if plays is not None:
        item["playCount"] = plays
    return item


def _muestra_viral(handle: str = "app.estudio") -> tuple[str, str]:
    piezas = [
        _pieza(0, 800_000, desc="fijada @otra.cuenta #studyapp"),
        _pieza(1, 10_000, desc="tambien fijada"),
    ]
    for indice in range(2, 11):
        piezas.append(_pieza(indice, 1_000, desc="reciente"))
    piezas.append(_pieza(11, 80_000, desc="esta app es la que vendemos @otra.cuenta"))
    bio = "Study tips. Download the app"
    return (
        _perfil(handle, bio=bio, enlace="https://studyapp.example"),
        _embed(handle, piezas),
    )


def _kwargs(red: _Red, settings):
    return {
        "transporte": red,
        "permitir_red": True,
        "sin_llm": True,
        "rng": __import__("random").Random(0),
        "dormir": lambda _segundos: None,
        "pausa": (0, 0),
    }


def test_la_mediana_ignora_fijadas_y_exige_suelo():
    piezas = []
    for indice, plays in enumerate([800_000, 10_000, *([1_000] * 9), 80_000]):
        piezas.append(
            Pieza(
                item_id=_id(1_700_000_000, indice),
                url=f"https://www.tiktok.com/@app.estudio/photo/{indice}",
                desc="study app",
                plays=plays,
                slideshow=True,
                created="2026-01-01",
            )
        )
    piezas.append(
        Pieza("x", "https://www.tiktok.com/@app.estudio/photo/x", "", None, True, None)
    )
    resultado = evaluar(
        piezas,
        bio="Download the app",
        enlace="https://studyapp.example",
        handle="app.estudio",
    )
    assert resultado.fijados == 3
    assert resultado.mediana_vistas == 1_000
    assert resultado.estado == "califica"
    assert resultado.num_piezas_virales == 2
    assert resultado.num_virales_no_fijadas == 1
    assert resultado.mejor_pieza_fijada == "sí"
    assert resultado.mejor_pieza_vistas == 800_000
    assert resultado.mejor_reciente_vistas == 80_000
    bajo = evaluar(
        [Pieza(str(i), "u", "", 1_000 if i else 40_000, True, None) for i in range(10)],
        bio="Download the app https://studyapp.example",
        handle="baja",
    )
    assert bajo.estado == "descartada"
    assert "alcance bajo" in bajo.motivo
    assert entero_vistas("1.2M") is None
    assert entero_vistas(None) is None
    assert entero_vistas("1200") == 1200


def test_no_califica_sin_producto_o_sin_slideshows():
    base = [
        Pieza(str(i), f"u{i}", "hola", 100_000 if i == 0 else 1_000, True, None) for i in range(10)
    ]
    sin_venta = evaluar(base, bio="just dancing", handle="baile")
    assert sin_venta.estado == "descartada"
    assert "no vende" in sin_venta.motivo
    pocas = [
        Pieza(str(i), f"u{i}", "app", 100_000 if i == 0 else 1_000, i < 4, None) for i in range(10)
    ]
    assert evaluar(pocas, bio="Download the app", handle="video").estado == "descartada"


def test_parsea_perfil_y_embed_sin_inventar_vistas():
    perfil = parsear_perfil(_perfil("app.estudio", bio="hola\nmundo", enlace="https://studyapp.example", seguidores=None), "app.estudio")
    assert perfil.seguidores is None
    assert perfil.enlace == "https://studyapp.example"
    assert " | " not in perfil.bio
    embed = parsear_embed(_embed("app.estudio", [_pieza(3, None), _pieza(4, 50_000, slideshow=False)]), "app.estudio")
    assert embed.piezas[0].plays is None
    assert embed.piezas[0].slideshow is True
    assert embed.piezas[1].slideshow is False
    assert embed.piezas[0].created is not None
    assert normalizar_handle("https://www.tiktok.com/@App.Estudio/photo/1") == "app.estudio"
    assert normalizar_handle("no vale espacio") == ""


def test_el_corte_se_importa_sin_duplicar_y_se_exporta_con_las_mismas_columnas(tmp_path):
    db = fresh_db(tmp_path)
    origen = Path(__file__).resolve().parents[1] / "radar" / "fixtures" / "cuentas-tiktok.csv"
    total = importar_csv(db, origen)
    assert total == 129
    assert importar_csv(db, origen) == 129
    assert len(listar_cuentas(db, estado="califica")) == 129
    cuenta = obtener_cuenta(db, "smartapp1505")
    assert cuenta is not None
    assert cuenta["nicho"] == "Autodesarrollo"
    assert cuenta["mejor_pieza_vistas"] == 4_400_000
    assert cuenta["fecha_detectada"] == "2026-11-24"
    assert cuenta["es_cuenta_slideshow"] == "sí"
    with db.connect() as conn:
        conn.execute("UPDATE tt_cuentas SET seguidores = 1, fecha_detectada = '2020-01-01' WHERE handle = 'smartapp1505'")
    importar_csv(db, origen)
    cuenta = obtener_cuenta(db, "smartapp1505")
    assert cuenta["seguidores"] == 2248
    assert cuenta["fecha_detectada"] == "2020-01-01"
    texto = exportar_csv(db)
    filas = list(csv.DictReader(texto.splitlines()))
    assert list(filas[0].keys()) == list(COLUMNAS_CSV)
    assert len(filas) == 129
    assert {fila["handle"] for fila in filas} == {cuenta["handle"] for cuenta in listar_cuentas(db)}
    otra = exportar_csv(db)
    assert texto == otra
    destino = tmp_path / "saliente.csv"
    exportar_csv(db, destino)
    assert destino.read_text(encoding="utf-8").splitlines()[0] == ",".join(COLUMNAS_CSV)


def test_descubre_por_mencion_lista_y_para_si_bloquean(settings, tmp_path):
    db = fresh_db(tmp_path)
    perfil, embed = _muestra_viral("app.estudio")
    red = _Red(
        {
            "https://www.tiktok.com/@app.estudio": Respuesta(200, perfil, "https://www.tiktok.com/@app.estudio"),
            "https://www.tiktok.com/embed/@app.estudio": Respuesta(200, embed, "embed"),
            "https://listas.test/apps": Respuesta(
                200,
                '<a href="https://www.tiktok.com/@app.estudio">lista</a>',
                "https://listas.test/apps",
            ),
            "https://www.tiktok.com/embed/tag/studyapp": Respuesta(
                200,
                '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
                '{"__DEFAULT_SCOPE__":{"webapp.hashtag":{"itemList":[{"author":{"uniqueId":"zz.tag","nickname":"T"}}]}}}'
                "</script>",
                "tag",
            ),
            "https://www.tiktok.com/@mala.cuenta": Respuesta(429, "no", "mala"),
        }
    )
    anadir_semilla(db, "url", "https://listas.test/apps")
    anadir_semilla(db, "hashtag", "studyapp")
    guardar_cuenta(
        db,
        {
            "handle": "mala.cuenta",
            "estado": "candidata",
            "semilla": "prueba",
            "fecha_detectada": "2026-11-01",
            "fecha_actualizada": "",
        },
    )
    opciones = _kwargs(red, settings)
    opciones["max_paginas"] = 20
    informe = ejecutar_descubrimiento(db, settings, max_cuentas=2, **opciones)
    assert informe.bloqueado is True
    assert "429" in informe.mensaje
    cuenta = obtener_cuenta(db, "app.estudio")
    assert cuenta is not None
    assert cuenta["estado"] == "califica"
    assert cuenta["seguidores"] == 1200
    assert cuenta["mediana_vistas"] == 1000
    assert cuenta["mejor_pieza_vistas"] == 800_000
    assert cuenta["nicho"] == "Estudio y educación"
    assert "REVISAR" in cuenta["clasificacion"]
    assert "studyapp.example" in cuenta["que_vende"]
    assert "TikTok público" in cuenta["fuente"]
    assert obtener_cuenta(db, "otra.cuenta")["estado"] == "candidata"
    assert obtener_cuenta(db, "zz.tag")["estado"] == "candidata"
    assert "https://api.apify.com" not in " ".join(red.urls)
    assert all(not url.startswith("https://api.apify.com") for url in red.urls)
    mala = obtener_cuenta(db, "mala.cuenta")
    assert mala["estado"] == "candidata"
    assert mala["seguidores"] is None
    assert mala["mediana_vistas"] is None


def test_actualizar_conserva_la_revision_manual_y_la_fecha(settings, tmp_path):
    db = fresh_db(tmp_path)
    perfil, embed = _muestra_viral("app.estudio")
    guardar_cuenta(
        db,
        {
            "handle": "app.estudio",
            "estado": "califica",
            "nicho": "Estudio y educación",
            "subnicho": "Apps de estudio",
            "que_vende": "StudyApp (revisada)",
            "tipo_producto": "app",
            "clasificacion": "revisada a mano (bio/pies de post)",
            "fecha_detectada": "2026-11-24",
            "fecha_actualizada": "2026-11-24",
            "seguidores": 10,
            "mejor_pieza_vistas": 10,
        },
    )
    red = _Red(
        {
            "https://www.tiktok.com/@app.estudio": Respuesta(200, perfil, "p"),
            "https://www.tiktok.com/embed/@app.estudio": Respuesta(200, embed, "e"),
        }
    )
    informe = ejecutar_actualizacion(db, settings, handle="app.estudio", **_kwargs(red, settings))
    assert informe.bloqueado is False
    cuenta = obtener_cuenta(db, "app.estudio")
    assert cuenta["que_vende"] == "StudyApp (revisada)"
    assert cuenta["clasificacion"].startswith("revisada a mano")
    assert cuenta["fecha_detectada"] == "2026-11-24"
    assert cuenta["seguidores"] == 1200
    assert cuenta["mejor_pieza_vistas"] == 800_000
    from radar.tiktok.descubrir import hoy

    assert cuenta["fecha_actualizada"] == hoy()


def test_si_el_embed_falla_no_se_borran_las_metricas(settings, tmp_path):
    db = fresh_db(tmp_path)
    guardar_cuenta(
        db,
        {
            "handle": "app.estudio",
            "estado": "califica",
            "seguidores": 10,
            "mediana_vistas": 5,
            "mejor_pieza_vistas": 90_000,
            "clasificacion": "revisada a mano (bio/pies de post)",
            "que_vende": "StudyApp",
            "nicho": "Estudio y educación",
            "fecha_detectada": "2026-11-24",
            "fecha_actualizada": "2026-11-24",
        },
    )
    perfil, _embed_html = _muestra_viral("app.estudio")
    red = _Red(
        {
            "https://www.tiktok.com/@app.estudio": Respuesta(200, perfil, "p"),
            "https://www.tiktok.com/embed/@app.estudio": Respuesta(200, "<html>vacio</html>", "e"),
        }
    )
    ejecutar_actualizacion(db, settings, handle="app.estudio", **_kwargs(red, settings))
    cuenta = obtener_cuenta(db, "app.estudio")
    assert cuenta["estado"] == "califica"
    assert cuenta["seguidores"] == 10
    assert cuenta["mediana_vistas"] == 5
    assert cuenta["mejor_pieza_vistas"] == 90_000


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

    modelo = _Modelo(
        '{"nicho":"Estudio y educación","subnicho":"Apps de estudio","que_vende":"StudyApp (en la bio)","tipo_producto":"app","idioma":"en"}'
    )
    etiqueta = Clasificador(modelo, "codex").clasificar(
        handle="app.estudio",
        bio="Download the app",
        enlace="https://studyapp.example",
        pies="",
        tipo_producto="app",
        idioma="en",
    )
    assert modelo.llamadas == 1
    assert etiqueta.que_vende == "StudyApp (en la bio)"
    assert etiqueta.clasificacion == "llm (codex)"
    roto = _Modelo("rompe")
    fallback = Clasificador(roto, "codex").clasificar(
        handle="app.estudio",
        bio="Download the app",
        enlace="https://studyapp.example",
        pies="study",
        tipo_producto="app",
        idioma="en",
    )
    assert fallback.clasificacion.startswith("heurística")
    assert fallback.nicho == "Estudio y educación"
    assert heuristica(handle="x", bio="justly pareja", enlace="", pies="", tipo_producto="app", idioma="en").nicho == "Relaciones y citas"


def test_apify_sin_token_no_pide_nada_y_no_guarda_sus_vistas(monkeypatch, settings, tmp_path):
    original = httpx.Client

    def boom(*_args, **_kwargs):
        raise AssertionError("no tenía que abrirse un cliente HTTP")

    monkeypatch.setattr(httpx, "Client", boom)
    saltado = buscar_handles(None, "clockworks/tiktok-scraper", ["study app"])
    assert saltado.llamado is False
    assert saltado.handles == []
    monkeypatch.setattr(httpx, "Client", original)

    vistas = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(str(request.url))
        assert "secreto" not in str(request.url)
        assert request.headers["authorization"] == "Bearer secreto"
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"data": {"id": "r1", "status": "SUCCEEDED", "defaultDatasetId": "d1"}})
        return httpx.Response(200, json=[{"authorMeta": {"name": "desde.apify"}, "playCount": 999999}])

    monkeypatch.setattr(httpx, "Client", httpx.Client)
    con_token = buscar_handles(
        "secreto",
        "clockworks/tiktok-scraper",
        ["study app"],
        transport=httpx.MockTransport(handler),
    )
    assert con_token.handles == ["desde.apify"]
    assert handles_de_items([{"playCount": 5}]) == []
    perfil, embed = _muestra_viral("desde.apify")
    red = _Red(
        {
            "https://www.tiktok.com/@desde.apify": Respuesta(200, perfil, "p"),
            "https://www.tiktok.com/embed/@desde.apify": Respuesta(200, embed, "e"),
        }
    )

    def falso_apify(_token, _actor, _consultas):
        from radar.tiktok.apify_opcional import ResultadoApify

        return ResultadoApify(handles=["desde.apify"], llamado=True, mensaje="solo handles")

    db = fresh_db(tmp_path)
    settings_token = settings
    monkeypatch.setenv("APIFY_TOKEN", "secreto")
    monkeypatch.setenv("RADAR_TT_APIFY_ACTOR", "clockworks/tiktok-scraper")
    from radar.config import load_settings

    ajustes = load_settings()
    informe = ejecutar_descubrimiento(
        db,
        ajustes,
        usar_apify=True,
        apify_buscar=falso_apify,
        max_cuentas=2,
        **_kwargs(red, settings_token),
    )
    assert informe.bloqueado is False
    cuenta = obtener_cuenta(db, "desde.apify")
    assert cuenta["mejor_pieza_vistas"] == 800_000
    assert "999999" not in (cuenta.get("piezas_virales") or "")


def test_sin_yes_no_hay_peticion_y_la_tarea_no_llama_a_schtasks(settings, tmp_path, monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise AssertionError("red")

    monkeypatch.setattr("radar.tiktok.fuente.HttpxTransporte", boom)
    monkeypatch.setattr("subprocess.run", boom)
    monkeypatch.chdir(tmp_path)
    assert main(["cuentas", "descubrir"]) == 0
    salida = capsys.readouterr().out
    assert "No se ha hecho ninguna petición" in salida
    assert main(["cuentas", "programar", "--hora", "07:30"]) == 0
    programado = capsys.readouterr().out
    assert "schtasks /Create" in programado
    assert "cuentas ciclo --yes" in programado
    script = tmp_path / "data" / "radar-cuentas-tiktok.ps1"
    assert script.is_file()
    assert "ciclo --yes" in script.read_text(encoding="utf-8")
    monkeypatch.setattr("radar.tiktok.programar._es_windows", lambda: False)
    programa = preparar_tarea(cwd=tmp_path, destino=tmp_path / "tarea.ps1", hora="07:30", python=r"C:\repo\.venv\Scripts\python.exe")
    assert "Windows" in instalar_tarea(programa)
    with pytest.raises(ValueError):
        preparar_tarea(hora="25:99")


def _html_tag(tag: str, cuantos: int = 20) -> str:
    videos = ",".join(
        '{"id":"%s","authorUniqueId":"%suser%d","desc":"photo","playCount":10,"coverUrl":"https://cdn/photomode/x"}'
        % (indice, tag, indice)
        for indice in range(cuantos)
    )
    return (
        '<script id="__FRONTITY_CONNECT_STATE__" type="application/json">'
        '{"source":{"data":{"/embed/tag/%s":{"videoList":[%s]}}}}</script>' % (tag, videos)
    )


class _RedTags(_Red):
    def get(self, url: str) -> Respuesta:
        self.urls.append(url)
        if "/embed/tag/" in url:
            tag = url.rstrip("/").rsplit("/", 1)[-1]
            return Respuesta(200, _html_tag(tag), url)
        return Respuesta(200, "<html></html>", url)


def test_el_embed_de_un_tag_encola_decenas(settings, tmp_path):
    from radar.tiktok.consultas import consulta_site, enlaces_de_listado, slug_de_nombre
    from radar.tiktok.parse import handles_publicos, motivo_sin_perfil
    from radar.tiktok.store import contar_por_estado

    assert slug_de_nombre("EasyStars") == "easystars"
    assert slug_de_nombre("Producto en Amazon") == ""
    assert 'site:tiktok.com/photo "EasyStars"' == consulta_site("EasyStars (easystars.ai)")
    assert motivo_sin_perfil("status 10221 ") == "cuenta privada o embed no disponible (status 10221)"
    html = _html_tag("easystars", 4)
    assert handles_publicos(html) == ["easystarsuser0", "easystarsuser1", "easystarsuser2", "easystarsuser3"]
    enlaces = enlaces_de_listado(
        '<a href="/tiktok/study-apps">a</a><a href="/app.js">b</a><a href="https://otro.test/tiktok/x">c</a>',
        "https://gr0w.app/tiktok",
    )
    assert enlaces == ["https://gr0w.app/tiktok/study-apps"]
    assert enlaces_de_listado(
        '<a href="/tag/slideshow?refer=1&amp;embed_source=1">t</a>',
        "https://www.tiktok.com/embed/tag/easystars",
    ) == []

    db = fresh_db(tmp_path)
    red = _RedTags()
    opciones = _kwargs(red, settings)
    opciones["max_paginas"] = 6
    informe = ejecutar_descubrimiento(db, settings, max_cuentas=0, **opciones)
    assert informe.bloqueado is False
    assert "No se ha hecho ninguna petición" not in informe.mensaje
    assert contar_por_estado(db).get("candidata", 0) >= 40


def _perfil_10221(handle: str) -> str:
    payload = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"statusCode": 10221, "statusMsg": ""}}}
    return (
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )


def test_el_10221_se_explica_y_no_tira_un_embed_con_piezas(settings, tmp_path):
    db = fresh_db(tmp_path)
    guardar_cuenta(db, {"handle": "cerrada", "estado": "candidata", "fecha_detectada": "2026-11-01", "fecha_actualizada": ""})
    red = _Red(
        {
            "https://www.tiktok.com/@cerrada": Respuesta(200, _perfil_10221("cerrada"), "p"),
            "https://www.tiktok.com/embed/@cerrada": Respuesta(200, "<html>sin embed</html>", "e"),
        }
    )
    opciones = _kwargs(red, settings)
    opciones["max_paginas"] = 0
    informe = ejecutar_descubrimiento(db, settings, max_cuentas=1, **opciones)
    assert informe.bloqueado is False
    cuenta = obtener_cuenta(db, "cerrada")
    assert cuenta["estado"] == "descartada"
    assert cuenta["motivo"] == "cuenta privada o embed no disponible (status 10221)"
    assert cuenta["seguidores"] is None
    assert cuenta["mediana_vistas"] is None

    db2 = fresh_db(tmp_path / "otro")
    _, embed = _muestra_viral("app.estudio")
    guardar_cuenta(db2, {"handle": "app.estudio", "estado": "candidata", "fecha_detectada": "2026-11-01", "fecha_actualizada": ""})
    red2 = _Red(
        {
            "https://www.tiktok.com/@app.estudio": Respuesta(200, _perfil_10221("app.estudio"), "p"),
            "https://www.tiktok.com/embed/@app.estudio": Respuesta(200, embed, "e"),
        }
    )
    opciones2 = _kwargs(red2, settings)
    opciones2["max_paginas"] = 0
    ejecutar_descubrimiento(db2, settings, max_cuentas=1, **opciones2)
    leida = obtener_cuenta(db2, "app.estudio")
    assert leida["estado"] == "califica"
    assert leida["seguidores"] is None
    assert leida["mejor_pieza_vistas"] == 800_000
    assert "10221" not in (leida["motivo"] or "")


def test_la_web_filtra_el_corte_por_nicho_y_vistas(settings):
    client = TestClient(create_app(settings))
    pagina = client.get("/tiktok")
    assert pagina.status_code == 200
    assert "smartapp1505" in pagina.text
    assert "slideshow" in pagina.text.lower() or "Slideshow" in pagina.text
    belleza = client.get("/tiktok", params={"nicho": "Belleza"})
    assert "anavale_647" in belleza.text
    assert "smartapp1505" not in belleza.text
    grandes = client.get("/tiktok", params={"vistas": "100000000"})
    assert "homedecorave" in grandes.text
    assert "smartapp1505" not in grandes.text
    ficha = client.get("/tiktok/smartapp1505")
    assert ficha.status_code == 200
    assert "Autodesarrollo" in ficha.text
    assert "Deepstash" in ficha.text
    assert client.get("/tiktok/no-existe").status_code == 404
    csv_resp = client.get("/tiktok/exportar.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.text.splitlines()[0] == ",".join(COLUMNAS_CSV)
    alta = client.post("/tiktok/semilla", data={"valor": "@semilla.nueva", "tipo": "handle"}, follow_redirects=False)
    assert alta.status_code == 303
    from radar.tiktok.store import listar_semillas

    from radar.db import Database

    db = Database(settings.db_path)
    assert any(semilla["valor"] == "semilla.nueva" for semilla in listar_semillas(db))
