"""Análisis de formato con un LLM configurable. El modelo no debe inventar cifras."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from radar.numbers import unexpected_numbers

HOOK_TYPES = (
    "pregunta",
    "afirmación contraintuitiva",
    "lista",
    "historia personal",
    "demostración",
    "error común",
    "comparación",
    "noticia",
    "otro",
)
BLOCK_IDS = ("gancho", "contexto", "conflicto", "giro", "cierre")

_SYSTEM = """Eres el editor de guiones de Antonio, creador de Almería.
Graba reels hablando a cámara sobre inteligencia artificial, negocios de una persona y Grok Bot.
Respondes solo con un objeto JSON, en español de España y de tú.

Reglas:
- No inventes cifras, porcentajes, dinero, fechas, seguidores ni resultados.
- Si una cifra no está escrita de forma literal en la transcripción o en el pie, no la copies y no la completes: deja [X].
- No copies al guion las reproducciones, los me gusta ni los comentarios del reel.
- Donde haga falta una experiencia, anécdota o dato real de Antonio, escribe exactamente [X].
- No atribuyas a Antonio hechos que no estén en el material.
- Clasifica el gancho con uno de estos tipos: pregunta, afirmación contraintuitiva, lista, historia personal, demostración, error común, comparación, noticia, otro.
- El formato siempre tiene estos bloques, en este orden: gancho, contexto, conflicto, giro, cierre.
"""

_USER_TEMPLATE = """Analiza este reel y devuelve JSON con esta forma exacta:
{
  "hook_type": "uno de la lista",
  "blocks": [
    {"id": "gancho", "texto": "plantilla reutilizable de este bloque", "en_pantalla": "qué se ve, o [X]"},
    {"id": "contexto", "texto": "...", "en_pantalla": "..."},
    {"id": "conflicto", "texto": "...", "en_pantalla": "..."},
    {"id": "giro", "texto": "...", "en_pantalla": "..."},
    {"id": "cierre", "texto": "...", "en_pantalla": "..."}
  ],
  "template": "plantilla reutilizable en texto, con [X] donde haya que rellenar",
  "adapted_script": "guion hablado para Antonio, con [X] donde haga falta su experiencia"
}

Cuenta de origen: @<<ACCOUNT>>
Pie de foto (puede estar vacío):
<<CAPTION>>

Transcripción:
<<TRANSCRIPT>>
"""


class LLMError(RuntimeError):
    """El proveedor no devolvió un análisis utilizable."""


@dataclass
class AnalysisDraft:
    hook_type: str
    blocks: list[dict[str, str]]
    template: str
    adapted_script: str
    warnings: list[str] = field(default_factory=list)
    raw: str = ""


class ChatClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def build_prompt(*, account: str, caption: str, transcript: str, perfil=None) -> tuple[str, str]:
    user = (
        _USER_TEMPLATE.replace("<<ACCOUNT>>", account or "desconocida")
        .replace("<<CAPTION>>", (caption or "").strip() or "(sin pie)")
        .replace("<<TRANSCRIPT>>", (transcript or "").strip() or "(sin transcripción)")
    )
    if perfil is not None:
        user += (
            "\nPerfil de Antonio (si un campo es [X], no lo completes):\n"
            f"Oferta: {perfil.hueco('oferta')}\n"
            f"Audiencia: {perfil.hueco('audiencia')}\n"
            f"Problema: {perfil.hueco('problema')}\n"
            f"CTA: {perfil.hueco('cta')}\n"
            f"Nicho: {perfil.hueco('nicho')}\n"
            f"Tono: {perfil.hueco('tono')}\n"
            f"Pruebas que sí se pueden citar: {perfil.hueco('pruebas')}\n"
            f"Restricciones: {perfil.hueco('restricciones')}\n"
        )
    return _SYSTEM, user


def parse_analysis(payload: str, *, source: str = "") -> AnalysisDraft:
    raw = (payload or "").strip()
    if not raw:
        raise LLMError("El modelo devolvió una respuesta vacía.")
    data = _load_json(raw)
    hook_type = _hook_type(data.get("hook_type"))
    blocks = _blocks(data.get("blocks"))
    template = _text(data.get("template")) or _template_from_blocks(blocks)
    script = _text(data.get("adapted_script")) or "[X]"
    warnings = _warnings(source, template, script)
    return AnalysisDraft(
        hook_type=hook_type,
        blocks=blocks,
        template=template,
        adapted_script=script,
        warnings=warnings,
        raw=raw,
    )


def analyze_with_client(
    client: ChatClient,
    *,
    account: str,
    caption: str,
    transcript: str,
    perfil=None,
) -> AnalysisDraft:
    system, user = build_prompt(account=account, caption=caption, transcript=transcript, perfil=perfil)
    try:
        raw = client.complete(system, user)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - el proveedor puede fallar de muchas formas
        raise LLMError(f"No se pudo llamar al modelo: {exc}") from exc
    source = f"{caption or ''}\n{transcript or ''}"
    return parse_analysis(raw, source=source)


class OpenAICompatibleClient:
    """Cliente mínimo para /chat/completions. Vale para OpenAI y APIs compatibles."""

    def __init__(self, *, api_key: str, base_url: str, model: str, transport: Any = None) -> None:
        import httpx

        self.model = model
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=90,
            transport=transport,
        )

    def complete(self, system: str, user: str) -> str:
        response = self._http.post(
            "/chat/completions",
            json={
                "model": self.model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        if response.status_code >= 400:
            raise LLMError(f"El proveedor respondió {response.status_code}.")
        body = response.json()
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("La respuesta no trae choices[0].message.content.") from exc

    def close(self) -> None:
        self._http.close()


class AnthropicClient:
    def __init__(self, *, api_key: str, model: str, transport: Any = None) -> None:
        import httpx

        self.model = model
        self._http = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=90,
            transport=transport,
        )

    def complete(self, system: str, user: str) -> str:
        response = self._http.post(
            "/v1/messages",
            json={
                "model": self.model,
                "max_tokens": 2500,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        if response.status_code >= 400:
            raise LLMError(f"Anthropic respondió {response.status_code}.")
        body = response.json()
        try:
            parts = body["content"]
            texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        except (KeyError, TypeError) as exc:
            raise LLMError("La respuesta de Anthropic no trae content.") from exc
        text = "\n".join(part for part in texts if part).strip()
        if not text:
            raise LLMError("Anthropic devolvió content vacío.")
        return text

    def close(self) -> None:
        self._http.close()


def _load_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("El modelo no devolvió JSON válido.") from exc
    if not isinstance(data, dict):
        raise LLMError("El JSON del modelo no es un objeto.")
    return data


def _hook_type(value: Any) -> str:
    text = _text(value).casefold()
    for name in HOOK_TYPES:
        if text == name.casefold():
            return name
    return "otro"


def _blocks(value: Any) -> list[dict[str, str]]:
    by_id: dict[str, dict[str, str]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            block_id = _text(item.get("id") or item.get("nombre")).casefold()
            if block_id not in BLOCK_IDS:
                continue
            by_id[block_id] = {
                "id": block_id,
                "texto": _text(item.get("texto") or item.get("text")) or "[X]",
                "en_pantalla": _text(item.get("en_pantalla") or item.get("on_screen")) or "[X]",
            }
    blocks: list[dict[str, str]] = []
    for block_id in BLOCK_IDS:
        blocks.append(by_id.get(block_id) or {"id": block_id, "texto": "[X]", "en_pantalla": "[X]"})
    return blocks


def _template_from_blocks(blocks: list[dict[str, str]]) -> str:
    lines = [f"- **{block['id'].capitalize()}:** {block['texto']}" for block in blocks]
    return "\n".join(lines)


def _warnings(source: str, template: str, script: str) -> list[str]:
    warnings: list[str] = []
    for label, text in (("la plantilla", template), ("el guion", script)):
        for number in unexpected_numbers(source, text):
            warnings.append(
                f"{label.capitalize()} incluye la cifra {number}, que no está en la transcripción ni en el pie. Revísala o cámbiala por [X]."
            )
    return warnings


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
