"""Apify solo si hay token, actor y se pide a propósito.

De un dataset solo se guardan handles. Las vistas que traiga el actor no se
copian: la cifra que queda en la base sale después del embed público.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar.apify_client import ApifyError, ApifyQuotaError
from radar.tiktok.reglas import normalizar_handle

API_BASE = "https://api.apify.com/v2"


@dataclass
class ResultadoApify:
    handles: list[str] = field(default_factory=list)
    llamado: bool = False
    mensaje: str = ""


def handles_de_items(items: list[Any]) -> list[str]:
    vistos: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for crudo in _candidatos(item):
            handle = normalizar_handle(str(crudo or ""))
            if handle and handle not in vistos:
                vistos.append(handle)
    return vistos


def _candidatos(item: dict) -> list[Any]:
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    author2 = item.get("author") if isinstance(item.get("author"), dict) else {}
    return [
        author.get("name"),
        author.get("uniqueId"),
        author2.get("uniqueId"),
        author2.get("name"),
        item.get("uniqueId"),
        item.get("authorUniqueId"),
    ]


def buscar_handles(
    token: str | None,
    actor: str | None,
    consultas: list[str],
    *,
    transport: Any = None,
) -> ResultadoApify:
    consultas_limpias = [consulta.strip() for consulta in consultas if consulta and consulta.strip()]
    if not token or not actor or not consultas_limpias:
        return ResultadoApify(
            mensaje="Apify no se usa: falta el token, el actor o una consulta. No se ha hecho ninguna petición.",
        )
    import httpx

    cliente = httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        transport=transport,
    )
    actor_id = actor.replace("/", "~")
    payload = {"searchQueries": consultas_limpias[:10], "resultsPerPage": 20}
    try:
        empezado = _json(cliente.post(f"/acts/{actor_id}/runs", params={"waitForFinish": 20}, json=payload))
        datos = empezado.get("data") if isinstance(empezado, dict) else None
        if not isinstance(datos, dict) or not datos.get("defaultDatasetId"):
            raise ApifyError("Apify no devolvió dataset de la búsqueda de TikTok.")
        if datos.get("status") != "SUCCEEDED":
            detalle = str(datos.get("statusMessage") or datos.get("status") or "sin estado")
            raise ApifyError(f"Apify no terminó la búsqueda ({detalle}). No se reintenta.")
        items = _json(cliente.get(f"/datasets/{datos['defaultDatasetId']}/items", params={"clean": "true"}))
        if not isinstance(items, list):
            raise ApifyError("El dataset de TikTok no es una lista.")
    except ApifyQuotaError as exc:
        return ResultadoApify(llamado=True, mensaje=str(exc))
    except (ApifyError, httpx.HTTPError) as exc:
        return ResultadoApify(llamado=True, mensaje=f"Apify no ha respondido ({exc}). Se sigue sin él.")
    finally:
        cliente.close()
    return ResultadoApify(
        handles=handles_de_items(items),
        llamado=True,
        mensaje="Handles leídos de Apify. Las vistas del actor no se guardan.",
    )


def _json(respuesta) -> Any:
    if respuesta.status_code == 402:
        raise ApifyQuotaError("Apify ha respondido 402. No se reintenta.")
    if respuesta.status_code >= 400:
        raise ApifyError(f"Apify ha respondido {respuesta.status_code}.")
    return respuesta.json()
