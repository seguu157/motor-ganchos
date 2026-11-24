"""Cliente HTTP del actor apify/instagram-reel-scraper. No se usa en los tests reales."""

from __future__ import annotations

import time
from typing import Any

from radar.apify_parse import build_actor_input

API_BASE = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(RuntimeError):
    """La ejecución del actor no terminó bien."""


class ApifyQuotaError(ApifyError):
    """Crédito, cuota o HTTP 402. No se reintenta Apify."""


class MissingApifyToken(RuntimeError):
    """No hay APIFY_TOKEN. No se ha hecho ninguna petición."""


class ApifyClient:
    def __init__(
        self,
        token: str,
        *,
        actor: str = "apify/instagram-reel-scraper",
        transport: Any = None,
        poll_seconds: float = 5.0,
        max_wait_seconds: float = 900.0,
    ) -> None:
        if not token:
            raise MissingApifyToken("Falta APIFY_TOKEN. No se ha hecho ninguna petición.")
        import httpx

        self.actor_id = actor.replace("/", "~")
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds
        self._http = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def fetch_reels(self, usernames: list[str], results_limit: int) -> list[dict[str, Any]]:
        payload = build_actor_input(usernames, results_limit)
        started = self._post(f"/acts/{self.actor_id}/runs", params={"waitForFinish": 30}, json=payload)
        run = started.get("data") if isinstance(started, dict) else None
        if not isinstance(run, dict) or not run.get("id"):
            raise ApifyError("Apify no devolvió el id de la ejecución.")
        run = self._wait(run)
        status = run.get("status")
        if status != "SUCCEEDED":
            detail = str(run.get("statusMessage") or "")
            if _text_is_quota(detail):
                raise ApifyQuotaError(
                    "Apify terminó sin crédito o sin cuota. No se reintenta."
                )
            raise ApifyError(f"La ejecución terminó en estado {status}.")
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise ApifyError("La ejecución no tiene dataset.")
        items = self._get(f"/datasets/{dataset_id}/items", params={"clean": "true"})
        if not isinstance(items, list):
            raise ApifyError("El dataset no es una lista.")
        return items

    def _wait(self, run: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        current = run
        while current.get("status") not in TERMINAL:
            if time.monotonic() - started > self.max_wait_seconds:
                raise ApifyError("La ejecución superó el tiempo de espera.")
            time.sleep(self.poll_seconds)
            payload = self._get(f"/actor-runs/{current['id']}")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise ApifyError("No se pudo leer el estado de la ejecución.")
            current = data
        return current

    def _post(self, path: str, *, params: dict[str, Any], json: dict[str, Any]) -> Any:
        response = self._http.post(path, params=params, json=json)
        return self._decode(response)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self._http.get(path, params=params)
        return self._decode(response)

    @staticmethod
    def _decode(response: Any) -> Any:
        if response.status_code >= 400:
            if response.status_code == 402 or _text_is_quota(_response_text(response)):
                raise ApifyQuotaError(
                    f"Apify respondió {response.status_code} (crédito o cuota). No se reintenta."
                )
            raise ApifyError(f"Apify respondió {response.status_code}.")
        return response.json()


def _response_text(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _text_is_quota(text: str) -> bool:
    lowered = text.lower()
    markers = ("quota", "credit", "insufficient", "usage hard limit", "payment required", "402")
    return any(marker in lowered for marker in markers)
