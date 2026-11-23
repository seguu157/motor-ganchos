"""Descarga el audio de un outlier y lo transcribe en local con faster-whisper.

El modelo no se carga al importar este módulo ni al abrir la web. Solo el
comando `radar run` lo hace, y únicamente si hay outliers sin transcripción.
Pensado para CPU, modelo small e int8, en un VPS de unos 2 GB: un reel cada vez.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radar.hooks import Segment, extract_hook

MAX_AUDIO_BYTES = 30 * 1024 * 1024


class WhisperNotInstalled(RuntimeError):
    """faster-whisper no está instalado en este entorno."""


class DownloadError(RuntimeError):
    """No se pudo bajar el audio."""


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str | None
    hook_text: str
    segments: list[dict[str, Any]]
    model: str


def choose_media_url(audio_url: str | None, video_url: str | None) -> str | None:
    """Prefiere el audio. El vídeo solo si el actor no mandó audioUrl."""
    for candidate in (audio_url, video_url):
        if candidate and candidate.startswith(("http://", "https://")):
            return candidate
    return None


def download_media(url: str, dest: Path, *, client: Any = None) -> Path:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    http = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        with http.stream("GET", url) as response:
            if response.status_code >= 400:
                raise DownloadError(f"La descarga respondió {response.status_code}.")
            size = 0
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise DownloadError("El archivo supera 30 MB. Se ha cortado para no llenar el VPS.")
                    handle.write(chunk)
    finally:
        if owns_client:
            http.close()
    if dest.stat().st_size == 0:
        raise DownloadError("El archivo descargado está vacío.")
    return dest


def transcribe_file(
    path: Path,
    *,
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "es",
) -> TranscriptResult:
    model = _load_model(model_name, device, compute_type)
    segments_iter, info = model.transcribe(str(path), language=language, vad_filter=True)
    segments: list[Segment] = []
    raw_segments: list[dict[str, Any]] = []
    parts: list[str] = []
    for segment in segments_iter:
        text = (segment.text or "").strip()
        segments.append(Segment(start=float(segment.start), end=float(segment.end), text=text))
        raw_segments.append({"start": float(segment.start), "end": float(segment.end), "text": text})
        if text:
            parts.append(text)
    full = " ".join(parts).strip()
    language_name = getattr(info, "language", None)
    return TranscriptResult(
        text=full,
        language=language_name,
        hook_text=extract_hook(full, segments),
        segments=raw_segments,
        model=model_name,
    )


def _load_model(model_name: str, device: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WhisperNotInstalled(
            "faster-whisper no está instalado. En el VPS: pip install -r requirements-whisper.txt "
            "(o construye la imagen con INSTALL_WHISPER=1). El modelo small se descarga en el primer uso."
        ) from exc
    return WhisperModel(model_name, device=device, compute_type=compute_type)
