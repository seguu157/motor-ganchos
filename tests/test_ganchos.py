"""Biblioteca de ganchos. No sale a la red: las lecturas son falsas."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from radar.cli import main
from radar.ganchos.agregar import rankings
from radar.ganchos.analizar import analizar, parsear_analisis
from radar.ganchos.exportar import exportar, render_csv, render_markdown
from radar.ganchos.extraer import ejecutar
from radar.ganchos.fotos import LecturaFoto, aplicar_ocr, documentos, imagenes_de_documento, leer_slideshow
from radar.ganchos.ig import ReelCrudo, reel_desde_blobs
from radar.ganchos.piezas import parsear_linea
from radar.ganchos.programar import instalar_tarea, preparar_tarea
from radar.ganchos.store import guardar_pieza, listar, obtener_por_url
from radar.instagram.fuente import InstagramBloqueado, mensaje_parada
from radar.llm_codex import CodexRateLimit
from radar.tiktok.fuente import Lector, Respuesta, TikTokBloqueado
from radar.tiktok.store import guardar_cuenta as guardar_tiktok
from radar.web import create_app
from tests.conftest import fresh_db


def _cuenta_tiktok(**extra):
    datos = {
        "handle": "app.estudio",
        "estado": "califica",
        "nicho": "Estudio y educación",
        "subnicho": "Apps de estudio",
        "que_vende": "StudyApp",
        "enlace_bio": "https://studyapp.example",
        "mediana_vistas": 1_000,
        "piezas_virales": (
            "https://www.tiktok.com/@app.estudio/photo/fijada | 800,000 vistas | 800.0× | fijado ; "
            "https://www.tiktok.com/@app.estudio/photo/alta | 80,000 vistas | 80.0× ; "
            "https://www.tiktok.com/@app.estudio/video/clip | 90,000 vistas | 90.0× | vídeo"
        ),
    }
    datos.update(extra)
    return datos


def _cuenta_ig():
    return {
        "handle": "creador.ia",
        "estado": "califica",
        "nicho": "IA",
        "subnicho": "Herramientas IA",
        "enlace": "https://ada.example",
        "mediana_vistas": 10_000,
        "piezas_virales": (
            "https://www.instagram.com/reel/PINNED1/ | 2,000,000 reproducciones | 200.0× | fijado ; "
            "https://www.instagram.com/reel/BIGREEL/ | 80,000 reproducciones | 8.0×"
        ),
    }


class _Red:
    def __init__(self, paginas: dict[str, Respuesta]):
        self.paginas = paginas
        self.urls: list[str] = []

    def get(self, url: str) -> Respuesta:
        self.urls.append(url)
        return self.paginas[url]

    def close(self) -> None:
        return None


def _pagina_foto() -> str:
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "desc": "Descarga la app en la bio",
                        "imagePost": {
                            "images": [
                                {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/a.jpeg"]}},
                                {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/b.jpeg"]}},
                            ]
                        },
                    }
                }
            }
        }
    }
    return (
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )


def test_la_linea_guarda_vistas_y_detecta_fijada():
    url, vistas, multiplo, fijada, slideshow = parsear_linea(
        "https://www.tiktok.com/@a/photo/1 | 1,382,086 vistas | 50.6× | fijado"
    )
    assert url.endswith("/photo/1")
    assert vistas == 1_382_086
    assert multiplo == 50.6
    assert fijada is True
    assert slideshow is True
    _, _, _, suelta, es_foto = parsear_linea("https://www.tiktok.com/@a/video/2 | 90,000 vistas | 3× | vídeo")
    assert suelta is False
    assert es_foto is False


def test_el_json_publico_trae_las_imagenes_y_el_ocr_marca_el_producto(tmp_path):
    html = _pagina_foto()
    docs = documentos(html)
    assert imagenes_de_documento(docs[0]) == [
        "https://p16.tiktokcdn.com/a.jpeg",
        "https://p16.tiktokcdn.com/b.jpeg",
    ]
    textos = {1: "Tres cosas que no te cuentan", 2: "Descarga StudyApp, link in bio"}

    def ocr(path: Path) -> str:
        indice = int(path.name.split("-")[1].split(".")[0])
        return textos[indice]

    def bajar(url: str, workdir: Path, indice: int) -> Path:
        destino = workdir / f"diapositiva-{indice}.img"
        destino.write_bytes(b"img")
        return destino

    lectura = LecturaFoto(
        caption="Descarga la app en la bio",
        imagenes=["https://p16.tiktokcdn.com/a.jpeg", "https://p16.tiktokcdn.com/b.jpeg"],
        origen="pagina",
    )
    diapositivas, producto, cta, aviso = aplicar_ocr(
        lectura,
        ocr=ocr,
        descargar=bajar,
        workdir=tmp_path,
        pistas=["StudyApp"],
    )
    assert aviso == ""
    assert producto == 2
    assert "StudyApp" in cta
    assert diapositivas[0]["texto"].startswith("Tres cosas")
    assert list(tmp_path.glob("*")) == []


def test_un_429_para_la_pasada_tras_un_reintento():
    red = _Red({"https://www.tiktok.com/@app.estudio/photo/alta": Respuesta(429, "no", "x")})
    lector = Lector(red, dormir=lambda _s: None, rng=__import__("random").Random(0), pausa_429=(0, 0))
    try:
        leer_slideshow(lector, "https://www.tiktok.com/@app.estudio/photo/alta", pausa=(0, 0), dormir=lambda _s: None, rng=__import__("random").Random(0))
    except TikTokBloqueado as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("tenía que parar")
    assert len(red.urls) == 2


def test_extraer_en_seco_no_pide_nada_y_salta_fijadas_y_lo_ya_leido(settings, tmp_path):
    db = fresh_db(tmp_path)
    guardar_tiktok(db, _cuenta_tiktok())
    from radar.instagram.store import guardar_cuenta as guardar_ig

    guardar_ig(db, _cuenta_ig())
    llamadas: list[str] = []

    def leer_tt(url: str) -> LecturaFoto:
        llamadas.append(url)
        return LecturaFoto(caption="pie", imagenes=["https://p16.tiktokcdn.com/a.jpeg"], origen="pagina")

    def leer_ig(url: str) -> ReelCrudo:
        llamadas.append(url)
        return ReelCrudo(url=url, caption="herramientas de IA", transcripcion="Para en tres segundos.", texto_pantalla="Pruébalo hoy")

    def ocr(_path: Path) -> str:
        return "Esto es la app StudyApp"

    def bajar(url: str, workdir: Path, indice: int) -> Path:
        destino = workdir / f"diapositiva-{indice}.img"
        destino.write_bytes(b"x")
        return destino

    seco = ejecutar(
        db,
        settings,
        fuente="todas",
        maximo=5,
        permitir_red=False,
        leer_tiktok=leer_tt,
        leer_instagram=leer_ig,
        ocr=ocr,
        descargar=bajar,
        dormir=lambda _s: None,
    )
    assert seco.status == "plan"
    assert llamadas == []
    assert "photo/alta" in seco.mensaje
    assert "photo/fijada" not in seco.mensaje
    assert "video/clip" not in seco.mensaje
    assert obtener_por_url(db, "tiktok", "https://www.tiktok.com/@app.estudio/photo/alta") is None

    hecho = ejecutar(
        db,
        settings,
        fuente="todas",
        maximo=5,
        permitir_red=True,
        leer_tiktok=leer_tt,
        leer_instagram=leer_ig,
        ocr=ocr,
        descargar=bajar,
        dormir=lambda _s: None,
    )
    assert hecho.status == "ok"
    assert hecho.leidas == 2
    assert set(llamadas) == {
        "https://www.tiktok.com/@app.estudio/photo/alta",
        "https://www.instagram.com/reel/BIGREEL/",
    }
    fijada = obtener_por_url(db, "tiktok", "https://www.tiktok.com/@app.estudio/photo/fijada")
    assert fijada is not None
    assert fijada["fijada"] is True
    assert fijada["estado"] == "omitida"
    assert fijada["diapositivas"] == []
    foto = obtener_por_url(db, "tiktok", "https://www.tiktok.com/@app.estudio/photo/alta")
    assert foto["estado"] == "extraida"
    assert foto["vistas"] == 80_000
    assert foto["multiplo"] == 80.0
    assert foto["num_diapositivas"] == 1
    assert foto["producto_indice"] == 1
    reel = obtener_por_url(db, "instagram", "https://www.instagram.com/reel/BIGREEL/")
    assert "tres segundos" in reel["gancho_texto"].casefold() or "Para" in reel["gancho_texto"]
    assert reel["caption"] == "herramientas de IA"
    assert "Pruébalo" in reel["texto_pantalla"]

    ejecutar(
        db,
        settings,
        fuente="todas",
        maximo=5,
        permitir_red=True,
        leer_tiktok=leer_tt,
        leer_instagram=leer_ig,
        ocr=ocr,
        descargar=bajar,
        dormir=lambda _s: None,
    )
    assert llamadas.count("https://www.tiktok.com/@app.estudio/photo/alta") == 1


def test_instagram_para_si_la_sesion_bloquea_y_no_transcribe_un_fijado(settings, tmp_path):
    db = fresh_db(tmp_path)
    from radar.instagram.store import guardar_cuenta as guardar_ig

    guardar_ig(
        db,
        {
            "handle": "creador.ia",
            "estado": "califica",
            "nicho": "IA",
            "subnicho": "Herramientas IA",
            "mediana_vistas": 10_000,
            "piezas_virales": (
                "https://www.instagram.com/reel/UNO111/ | 80,000 reproducciones | 8.0× ; "
                "https://www.instagram.com/reel/DOS222/ | 70,000 reproducciones | 7.0×"
            ),
        },
    )
    vistos: list[str] = []

    def leer(url: str) -> ReelCrudo:
        vistos.append(url)
        if url.endswith("DOS222/"):
            raise InstagramBloqueado(mensaje_parada("429"))
        return ReelCrudo(url=url, caption="IA", transcripcion="Mira esto.", fijado=False)

    informe = ejecutar(
        db,
        settings,
        fuente="instagram",
        maximo=5,
        permitir_red=True,
        leer_instagram=leer,
        dormir=lambda _s: None,
    )
    assert informe.bloqueado is True
    assert "429" in informe.mensaje
    assert len(vistos) == 2
    assert obtener_por_url(db, "instagram", "https://www.instagram.com/reel/UNO111/")["estado"] == "extraida"
    assert obtener_por_url(db, "instagram", "https://www.instagram.com/reel/DOS222/") is None

    blobs = [
        {
            "code": "UNO111",
            "product_type": "clips",
            "caption": {"text": "pie del reel"},
            "video_url": "https://cdn.example/v.mp4",
            "accessibility_caption": "Texto en pantalla",
            "clips_tab_pinned_user_ids": [9],
        }
    ]
    crudo = reel_desde_blobs(blobs, "https://www.instagram.com/reel/UNO111/")
    assert crudo.fijado is True
    assert crudo.caption == "pie del reel"
    assert crudo.texto_pantalla == "Texto en pantalla"


def test_analizar_salta_lo_hecho_y_para_en_el_limite(settings, tmp_path):
    db = fresh_db(tmp_path)
    primera = guardar_pieza(
        db,
        {
            "fuente": "tiktok",
            "url": "https://www.tiktok.com/@a/photo/1",
            "handle": "a",
            "nicho": "Estudio y educación",
            "subnicho": "Apps de estudio",
            "vistas": 80_000,
            "mediana": 1_000,
            "multiplo": 80.0,
            "caption": "Descarga la app",
            "diapositivas": [{"indice": 1, "texto": "Tres cosas", "origen": "ocr"}],
            "gancho_texto": "Tres cosas",
            "estado": "extraida",
            "extraida_en": "2026-11-25T00:00:00+00:00",
        },
    )
    guardar_pieza(
        db,
        {
            "fuente": "instagram",
            "url": "https://www.instagram.com/reel/BIGREEL/",
            "handle": "creador.ia",
            "nicho": "IA",
            "subnicho": "Herramientas IA",
            "vistas": 80_000,
            "mediana": 10_000,
            "multiplo": 8.0,
            "caption": "herramientas de IA",
            "transcripcion": "Para en tres segundos.",
            "gancho_texto": "Para en tres segundos.",
            "estado": "extraida",
            "extraida_en": "2026-11-25T00:00:00+00:00",
        },
    )
    guardar_pieza(
        db,
        {
            "fuente": "tiktok",
            "url": "https://www.tiktok.com/@a/photo/fijada",
            "handle": "a",
            "vistas": 900_000,
            "multiplo": 900.0,
            "fijada": True,
            "estado": "omitida",
        },
    )

    class _Modelo:
        def __init__(self) -> None:
            self.n = 0

        def complete(self, _sistema: str, _usuario: str) -> str:
            self.n += 1
            if self.n == 2:
                raise CodexRateLimit("límite")
            return json.dumps(
                {
                    "hook": "Tres cosas",
                    "hook_type": "lista",
                    "formato": "lista",
                    "estructura": "Una lista de tres pasos y el producto al final.",
                    "insercion_producto": "En la última diapositiva.",
                    "angulo": "Curiosidad.",
                    "por_que": "El material abre con una lista.",
                    "plantilla": "Diapositiva 1: [X]\nDiapositiva 2: [X]",
                    "adaptacion": "Sugerencia: cuenta tres usos de [X] y cierra con tu CTA.",
                },
                ensure_ascii=False,
            )

        def close(self) -> None:
            return None

    modelo = _Modelo()
    parado = analizar(db, settings, permitir=True, cliente=modelo, dormir=lambda _s: None)
    assert parado.parado is True
    assert parado.analizados == 1
    assert modelo.n == 2
    guardadas = listar(db)
    assert len(guardadas) == 2
    hecha = next(pieza for pieza in guardadas if pieza["id"] == primera)
    assert hecha["formato"] == "lista"
    assert hecha["adaptacion"].startswith("Sugerencia:")
    pendiente = next(pieza for pieza in guardadas if "BIGREEL" in pieza["url"])
    assert not pendiente["formato"]

    class _Sigue:
        def complete(self, _sistema: str, usuario: str) -> str:
            assert "BIGREEL" in usuario
            assert "900" not in usuario
            return json.dumps(
                {
                    "hook": "Para en tres segundos.",
                    "hook_type": "pregunta",
                    "formato": "tutorial",
                    "estructura": "Promesa y demostración.",
                    "insercion_producto": "No se nombra un producto.",
                    "angulo": "Prisa.",
                    "por_que": "La primera frase corta el scroll.",
                    "plantilla": "En tres segundos, [X].",
                    "adaptacion": "Hazlo con Grok Bot y [X].",
                }
            )

        def close(self) -> None:
            return None

    seguido = analizar(db, settings, permitir=True, cliente=_Sigue(), dormir=lambda _s: None)
    assert seguido.analizados == 1
    assert seguido.pendientes == 0
    otra = analizar(db, settings, permitir=True, cliente=_Sigue(), dormir=lambda _s: None)
    assert otra.analizados == 0
    assert "No hay piezas pendientes" in otra.mensaje

    piezas = listar(db)
    filas = rankings(piezas, "formato")
    assert {fila["etiqueta"] for fila in filas} == {"lista", "tutorial"}
    lista = next(fila for fila in filas if fila["etiqueta"] == "lista")
    assert lista["n"] == 1
    assert lista["mediana_multiplo"] == 80.0
    assert lista["piezas"][0]["url"].endswith("/photo/1")
    assert all("fijada" not in fila["piezas"][0]["url"] for fila in filas)
    markdown = render_markdown(piezas)
    assert "https://www.tiktok.com/@a/photo/1" in markdown
    assert "Sugerencia:" in markdown
    assert "photo/fijada" not in markdown
    assert "80,0x" in markdown
    csv_texto = render_csv(piezas)
    assert "photo/fijada" not in csv_texto
    assert csv_texto.splitlines()[0].startswith("fuente,url,")

    aviso = parsear_analisis(
        json.dumps(
            {
                "hook": "Tres",
                "hook_type": "lista",
                "formato": "listicle",
                "plantilla": "Sube un 40 por ciento con [X].",
                "adaptacion": "Sugerencia: [X]",
                "por_que": "Porque sí.",
            }
        ),
        fuente="Tres cosas",
    )
    assert aviso["formato"] == "lista"
    assert aviso["warnings"]
    assert "40" in aviso["warnings"][0]


def test_la_web_filtra_y_el_ranking_enlaza_la_pieza(settings, tmp_path):
    db = fresh_db(tmp_path)
    guardar_pieza(
        db,
        {
            "fuente": "tiktok",
            "url": "https://www.tiktok.com/@a/photo/1",
            "handle": "a",
            "nicho": "Estudio y educación",
            "subnicho": "Apps de estudio",
            "vistas": 80_000,
            "mediana": 1_000,
            "multiplo": 80.0,
            "gancho_texto": "Tres cosas",
            "estado": "extraida",
            "extraida_en": "2026-11-25T00:00:00+00:00",
        },
    )
    from radar.ganchos.store import guardar_analisis

    pieza = obtener_por_url(db, "tiktok", "https://www.tiktok.com/@a/photo/1")
    guardar_analisis(
        db,
        pieza["id"],
        {
            "hook": "Tres cosas",
            "hook_type": "lista",
            "formato": "lista",
            "plantilla": "Diapositiva 1: [X]",
            "adaptacion": "Sugerencia: adapta la lista a [X].",
        },
    )
    client = TestClient(create_app(settings))
    pagina = client.get("/ganchos")
    assert pagina.status_code == 200
    assert "Tres cosas" in pagina.text
    assert "https://www.tiktok.com/@a/photo/1" in pagina.text
    assert "Sugerencia:" in pagina.text
    assert client.get("/ganchos?fuente=instagram").text.count("Tres cosas") == 0
    formatos = client.get("/ganchos?formato=lista")
    assert "Tres cosas" in formatos.text
    ranking = client.get("/ganchos/formatos")
    assert ranking.status_code == 200
    assert "lista" in ranking.text
    assert "https://www.tiktok.com/@a/photo/1" in ranking.text
    assert "80,0x" in ranking.text


def test_cli_exporta_y_programa_sin_schtasks(settings, tmp_path, monkeypatch, capsys):
    db = fresh_db(tmp_path)
    monkeypatch.setenv("RADAR_DB", str(db.path))
    guardar_pieza(
        db,
        {
            "fuente": "instagram",
            "url": "https://www.instagram.com/reel/BIGREEL/",
            "handle": "creador.ia",
            "vistas": 80_000,
            "multiplo": 8.0,
            "gancho_texto": "Para.",
            "estado": "extraida",
            "extraida_en": "2026-11-25T00:00:00+00:00",
        },
    )
    destino = tmp_path / "salida"
    assert main(["ganchos", "exportar", "--out", str(destino)]) == 0
    assert (destino / "ganchos.md").is_file()
    assert "BIGREEL" in (destino / "ganchos.md").read_text(encoding="utf-8")
    assert "BIGREEL" in (destino / "ganchos.csv").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["ganchos", "extraer", "--fuente", "tiktok", "--max", "1"]) == 0
    assert "no hay ninguna petición" in capsys.readouterr().out.casefold() or "Sin --yes" in capsys.readouterr().out
    assert main(["ganchos", "programar", "--hora", "09:15"]) == 0
    texto = capsys.readouterr().out
    assert "schtasks /Create" in texto
    assert "ganchos ciclo --yes" in texto
    script = tmp_path / "data" / "radar-ganchos.ps1"
    assert "ganchos ciclo --yes" in script.read_text(encoding="utf-8")
    programa = preparar_tarea(cwd=tmp_path, destino=tmp_path / "tarea.ps1", hora="09:15")
    assert "Windows" in instalar_tarea(programa)
    md, _csv = exportar(db, str(tmp_path / "uno.md"))
    assert md is not None and md.suffix == ".md"
