"""Configuración leída de variables de entorno. Los secretos no se guardan en SQLite."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from radar.cost import DEFAULT_MONTHLY_CREDIT_USD, DEFAULT_USD_PER_1000


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        return
    _load(override=False)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    apify_token: str | None
    apify_actor: str
    reels_per_account: int
    outlier_multiple: float
    sample_size: int
    usd_per_1000: Decimal
    monthly_credit_usd: Decimal
    llm_provider: str
    llm_model: str | None
    openai_api_key: str | None
    openai_base_url: str
    anthropic_api_key: str | None
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_language: str
    tubealfred_api_key: str | None
    host: str
    port: int
    radar_source: str
    ig_username: str | None
    ig_session_file: Path
    ig_max_accounts: int

    @property
    def llm_api_key(self) -> str | None:
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key
        if self.llm_provider == "openai":
            return self.openai_api_key
        return None

    @property
    def llm_ready(self) -> bool:
        return self.llm_provider in {"openai", "anthropic"} and bool(self.llm_api_key) and bool(self.llm_model)

    @property
    def ig_ready(self) -> bool:
        return bool(self.ig_username) and self.ig_session_file.is_file()


def load_settings() -> Settings:
    load_dotenv()
    provider = _provider()
    return Settings(
        db_path=Path(os.environ.get("RADAR_DB", "data/radar.sqlite")),
        apify_token=_blank(os.environ.get("APIFY_TOKEN")),
        apify_actor=os.environ.get("APIFY_ACTOR", "apify/instagram-reel-scraper"),
        reels_per_account=_int("RADAR_REELS_PER_ACCOUNT", 20, minimum=1),
        outlier_multiple=_float("RADAR_OUTLIER_MULTIPLE", 2.0),
        sample_size=_int("RADAR_SAMPLE_SIZE", 20, minimum=1),
        usd_per_1000=_decimal("APIFY_USD_PER_1000", DEFAULT_USD_PER_1000),
        monthly_credit_usd=_decimal("APIFY_MONTHLY_CREDIT_USD", DEFAULT_MONTHLY_CREDIT_USD),
        llm_provider=provider,
        llm_model=_blank(os.environ.get("LLM_MODEL")),
        openai_api_key=_blank(os.environ.get("OPENAI_API_KEY")),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        anthropic_api_key=_blank(os.environ.get("ANTHROPIC_API_KEY")),
        whisper_model=os.environ.get("WHISPER_MODEL", "small"),
        whisper_device=os.environ.get("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        whisper_language=os.environ.get("WHISPER_LANGUAGE", "es"),
        tubealfred_api_key=_blank(os.environ.get("TUBEALFRED_API_KEY")),
        host=os.environ.get("RADAR_HOST", "127.0.0.1"),
        port=_int("RADAR_PORT", 8000, minimum=1),
        radar_source=_source_mode(),
        ig_username=_blank(os.environ.get("IG_USERNAME")),
        ig_session_file=Path(os.environ.get("IG_SESSION_FILE", "data/ig.session")),
        ig_max_accounts=_int("RADAR_IG_MAX_ACCOUNTS", 10, minimum=1),
    )


def _source_mode() -> str:
    mode = (os.environ.get("RADAR_SOURCE") or "auto").strip().lower()
    if mode not in {"auto", "apify", "instaloader"}:
        raise ValueError("RADAR_SOURCE tiene que ser auto, apify o instaloader.")
    return mode


def _provider() -> str:
    explicit = _blank(os.environ.get("LLM_PROVIDER"))
    if explicit:
        name = explicit.lower()
        if name not in {"openai", "anthropic", "none"}:
            raise ValueError("LLM_PROVIDER tiene que ser openai, anthropic o none.")
        return name
    if _blank(os.environ.get("OPENAI_API_KEY")):
        return "openai"
    if _blank(os.environ.get("ANTHROPIC_API_KEY")):
        return "anthropic"
    return "none"


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} tiene que ser un entero.") from exc
    if number < minimum:
        raise ValueError(f"{name} tiene que ser >= {minimum}.")
    return number


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{name} tiene que ser un número.") from exc


def _decimal(name: str, default: Decimal) -> Decimal:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return Decimal(raw.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"{name} tiene que ser un importe.") from exc
