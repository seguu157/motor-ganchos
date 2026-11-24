"""Backend de análisis con Codex CLI y la suscripción de ChatGPT.

No usa OPENAI_API_KEY. Llama a `codex exec` en modo no interactivo, con el
sandbox en solo lectura, y lee el último mensaje. La documentación del CLI
(https://learn.chatgpt.com/docs/cli/reference) describe estas opciones:

- `codex exec -` lee el prompt por stdin
- `--skip-git-repo-check` permite un directorio que no es un repo
- `--sandbox read-only` impide que el agente escriba
- `--json` emite eventos JSONL
- `--output-last-message` guarda el mensaje final
- `--model` es opcional (`RADAR_CODEX_MODEL`)
- `codex login status` sale con 0 si hay sesión
- `codex login --device-auth` es el alta en un VPS sin navegador
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from radar.llm import LLMError, parse_analysis

LOGIN_HINT = (
    "Ejecuta `codex login` en este ordenador. "
    "En un VPS sin navegador: `codex login --device-auth`."
)


class CodexNotInstalled(LLMError):
    """El comando `codex` no está en el PATH."""


class CodexNotLoggedIn(LLMError):
    """Codex no tiene una sesión de ChatGPT."""


class CodexRateLimit(LLMError):
    """El plan de ChatGPT ha llegado al límite. Hay que parar la tanda."""


Runner = Callable[..., Any]


def build_exec_command(
    *,
    binary: str,
    output_path: Path,
    workdir: Path,
    model: str | None,
) -> list[str]:
    command = [
        binary,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--color",
        "never",
        "--json",
        "--output-last-message",
        str(output_path),
        "--cd",
        str(workdir),
    ]
    if model:
        command.extend(["--model", model])
    command.append("-")
    return command


class CodexClient:
    """Una llamada cada vez. El reintento de JSON inválido es solo uno."""

    def __init__(
        self,
        *,
        binary: str = "codex",
        model: str | None = None,
        timeout: float = 180,
        runner: Runner | None = None,
    ) -> None:
        self.binary = binary
        self.model = model
        self.timeout = timeout
        self._runner = runner or _default_runner
        self._login_checked = False

    def close(self) -> None:
        return None

    def complete(self, system: str, user: str) -> str:
        self.ensure_logged_in()
        prompt = (
            f"{system}\n\n{user}\n\n"
            "Responde únicamente con un objeto JSON válido, sin markdown ni texto alrededor."
        )
        last_error: LLMError | None = None
        for attempt in (1, 2):
            text = self._exec(prompt)
            try:
                parse_analysis(text, source=user)
            except CodexRateLimit:
                raise
            except LLMError as exc:
                last_error = exc
                if attempt == 1:
                    prompt = (
                        f"{prompt}\n\n"
                        "La respuesta anterior no era JSON válido. "
                        "Devuelve solo el objeto JSON pedido."
                    )
                    continue
                raise
            return text
        raise last_error or LLMError("Codex no devolvió JSON válido.")

    def ensure_logged_in(self) -> None:
        if self._login_checked:
            return
        try:
            result = self._runner([self.binary, "login", "status"], input_text=None)
        except FileNotFoundError as exc:
            raise _not_installed() from exc
        if _returncode(result) != 0 or _looks_logged_out(_combined(result)):
            detail = _combined(result).strip()
            extra = f" Detalle: {detail}" if detail else ""
            raise CodexNotLoggedIn(f"Codex no tiene sesión de ChatGPT. {LOGIN_HINT}{extra}")
        self._login_checked = True

    def _exec(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="radar-codex-") as folder:
            workdir = Path(folder)
            output_path = workdir / "ultimo-mensaje.txt"
            command = build_exec_command(
                binary=self.binary,
                output_path=output_path,
                workdir=workdir,
                model=self.model,
            )
            try:
                result = self._runner(command, input_text=prompt, timeout=self.timeout)
            except FileNotFoundError as exc:
                raise _not_installed() from exc
            stdout = _stream(result, "stdout")
            stderr = _stream(result, "stderr")
            combined = f"{stdout}\n{stderr}"
            if _looks_like_rate_limit(combined):
                raise CodexRateLimit(
                    "Límite de uso del plan ChatGPT. "
                    "Los reels que faltan quedan pendientes para la próxima tanda."
                )
            if _returncode(result) != 0 and _looks_logged_out(combined):
                raise CodexNotLoggedIn(f"Codex rechazó la sesión. {LOGIN_HINT}")
            if _returncode(result) != 0:
                detail = stderr.strip() or stdout.strip() or f"código {_returncode(result)}"
                raise LLMError(f"codex exec falló: {detail}")
            message = _final_message(output_path, stdout)
            if _looks_like_rate_limit(message):
                try:
                    parse_analysis(message, source="")
                except LLMError as exc:
                    raise CodexRateLimit(
                        "Límite de uso del plan ChatGPT. "
                        "Los reels que faltan quedan pendientes para la próxima tanda."
                    ) from exc
            return message


def client_for_settings(settings: Any) -> Any:
    if settings.llm_provider == "codex":
        return CodexClient(binary=settings.codex_bin, model=settings.codex_model, timeout=settings.codex_timeout)
    if settings.llm_provider == "anthropic":
        from radar.llm import AnthropicClient

        return AnthropicClient(api_key=settings.anthropic_api_key or "", model=settings.llm_model or "")
    from radar.llm import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key=settings.openai_api_key or "",
        base_url=settings.openai_base_url,
        model=settings.llm_model or "",
    )


def check_backend(settings: Any, *, client: Any = None) -> str:
    """Una petición mínima para saber si el backend configurado responde."""
    if settings.llm_provider == "none":
        return "LLM desactivado (RADAR_LLM=none)."
    owns = False
    if client is None:
        client = client_for_settings(settings)
        owns = True
    system = (
        "Responde solo con un objeto JSON. No inventes cifras. "
        'Usa hook_type "otro", template "[X]" y adapted_script "[X]".'
    )
    user = "Comprobación de radar. No hay transcripción."
    try:
        raw = client.complete(system, user)
        draft = parse_analysis(raw, source=user)
    finally:
        close = getattr(client, "close", None)
        if owns and close:
            close()
    return f"Backend {settings.llm_provider} respondió. Tipo de prueba: {draft.hook_type}."


def _default_runner(
    args: list[str],
    input_text: str | None = None,
    timeout: float = 180,
    **_kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _not_installed() -> CodexNotInstalled:
    return CodexNotInstalled(
        "No está el comando `codex`. Instala Node y `@openai/codex` "
        "(`npm install -g @openai/codex`). Después, `codex login` o, "
        f"en un VPS, `codex login --device-auth`."
    )


def _returncode(result: Any) -> int:
    return int(getattr(result, "returncode", 1))


def _stream(result: Any, name: str) -> str:
    value = getattr(result, name, "") or ""
    return str(value)


def _combined(result: Any) -> str:
    return f"{_stream(result, 'stdout')}\n{_stream(result, 'stderr')}"


def _looks_logged_out(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "not logged in",
        "not logged-in",
        "please log in",
        "login required",
        "no credentials",
        "unauthorized",
        "authentication required",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_rate_limit(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "rate limit",
        "rate_limit",
        "usage limit",
        "usage_limit",
        "too many requests",
        "limit exceeded",
        "quota exceeded",
        "http 429",
        " 429",
        "error 429",
    )
    return any(marker in lowered for marker in markers)


def _final_message(output_path: Path, stdout: str) -> str:
    if output_path.is_file():
        written = output_path.read_text(encoding="utf-8").strip()
        if written:
            return written
    last = ""
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        blob = json.dumps(event)
        if event.get("type") in {"error", "turn.failed"} and _looks_like_rate_limit(blob):
            raise CodexRateLimit(
                "Límite de uso del plan ChatGPT. "
                "Los reels que faltan quedan pendientes para la próxima tanda."
            )
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item.get("type") == "agent_message" and item.get("text"):
            last = str(item["text"])
    return last or stdout.strip()
