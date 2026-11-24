import pytest

from radar.config import load_settings
from radar.db import Database


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    for key in (
        "APIFY_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "RADAR_LLM",
        "RADAR_CODEX_MODEL",
        "RADAR_LLM_DELAY_SECONDS",
        "TUBEALFRED_API_KEY",
        "APIFY_USD_PER_1000",
        "APIFY_MONTHLY_CREDIT_USD",
        "RADAR_SOURCE",
        "IG_USERNAME",
        "IG_SESSION_FILE",
        "RADAR_IG_MAX_ACCOUNTS",
    ):
        monkeypatch.delenv(key, raising=False)


def make_settings(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("RADAR_DB", str(tmp_path / "radar.sqlite"))
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))
    return load_settings()


def fresh_db(tmp_path) -> Database:
    return Database(tmp_path / "radar.sqlite")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    return make_settings(monkeypatch, tmp_path)
