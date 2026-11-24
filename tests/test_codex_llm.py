import json
import sys
from types import SimpleNamespace

import pytest

from radar.apify_parse import parse_item
from radar.config import load_settings
from radar.db import Database
from radar.llm_codex import (
    CodexNotInstalled,
    CodexNotLoggedIn,
    CodexRateLimit,
    _default_runner,
    argv_ejecutable,
    build_exec_command,
    check_backend,
)
from radar.pipeline import analyze_pending
from tests.conftest import make_settings

VALID = '{"hook_type":"otro","template":"[X]","adapted_script":"[X]"}'


def _result(code=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def _write_last_message(args, text):
    path = args[args.index("--output-last-message") + 1]
    from pathlib import Path

    Path(path).write_text(text, encoding="utf-8")


def test_comando_de_codex_es_de_solo_lectura_y_lee_stdin(tmp_path):
    command = build_exec_command(
        binary="codex",
        output_path=tmp_path / "ultimo.txt",
        workdir=tmp_path,
        model="modelo-de-prueba",
    )
    assert command[0:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--json" in command
    assert "--output-last-message" in command
    assert command[command.index("--model") + 1] == "modelo-de-prueba"
    assert command[-1] == "-"


def test_llm_check_con_subprocess_simulado(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RADAR_DB", str(tmp_path / "radar.sqlite"))
    calls = []

    def fake_run(args, input=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        calls.append((list(args), input))
        if args[1:3] == ["login", "status"]:
            return _result(0, "Logged in using ChatGPT\n")
        assert args[-1] == "-"
        assert "--skip-git-repo-check" in args
        assert args[args.index("--sandbox") + 1] == "read-only"
        assert "JSON" in (input or "")
        _write_last_message(args, VALID)
        return _result(0, "")

    monkeypatch.setattr("radar.llm_codex.subprocess.run", fake_run)
    from radar.cli import main

    assert main(["llm-check"]) == 0
    captured = capsys.readouterr()
    assert "codex" in captured.out
    assert "otro" in captured.out
    assert any(item[0][1:3] == ["login", "status"] for item in calls)
    execs = [item for item in calls if item[0][1] == "exec"]
    assert len(execs) == 1


def test_reintenta_una_vez_si_el_json_no_vale(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, RADAR_CODEX_MODEL="modelo-de-prueba")
    assert settings.llm_provider == "codex"
    state = {"exec": 0}

    def fake_run(args, input=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        if args[1:3] == ["login", "status"]:
            return _result(0, "Logged in using ChatGPT\n")
        state["exec"] += 1
        assert "--model" in args
        assert args[args.index("--model") + 1] == "modelo-de-prueba"
        _write_last_message(args, "no es json" if state["exec"] == 1 else VALID)
        return _result(0, "")

    monkeypatch.setattr("radar.llm_codex.subprocess.run", fake_run)
    message = check_backend(settings)
    assert state["exec"] == 2
    assert "otro" in message


def test_sin_binario_o_sin_sesion_explica_el_login(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr("radar.llm_codex.subprocess.run", missing)
    with pytest.raises(CodexNotInstalled) as not_installed:
        check_backend(settings)
    assert "codex login" in str(not_installed.value)
    assert "--device-auth" in str(not_installed.value)

    def logged_out(args, input=None, **_kwargs):
        return _result(1, "", "Not logged in")

    monkeypatch.setattr("radar.llm_codex.subprocess.run", logged_out)
    with pytest.raises(CodexNotLoggedIn) as logged:
        check_backend(settings)
    assert "codex login" in str(logged.value)
    assert "--device-auth" in str(logged.value)


def test_limite_de_uso_deja_el_resto_pendiente(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, RADAR_LLM_DELAY_SECONDS="2")
    db = Database(settings.db_path)
    account_id = db.ensure_account("jordigpt", active=True)
    ids = []
    for index, plays in enumerate((10, 10, 10, 40, 50)):
        reel = parse_item(
            {
                "id": f"c{index}",
                "shortCode": f"C{index}",
                "url": f"https://www.instagram.com/reel/C{index}/",
                "videoPlayCount": plays,
                "timestamp": f"2026-11-{index + 1:02d}T00:00:00+00:00",
                "ownerUsername": "jordigpt",
                "caption": "[EJEMPLO] no es real",
                "likesCount": 1,
                "commentsCount": 1,
            }
        )
        reel_id = db.upsert_reel(account_id, reel, is_example=True, run_id=None)
        if plays >= 40:
            db.save_transcript(
                reel_id,
                text="Esto es una transcripción de ejemplo, sin cifras.",
                language="es",
                hook_text="Esto es una transcripción de ejemplo, sin cifras.",
                segments=[],
                model="test",
            )
            ids.append(reel_id)
    db.recompute_outliers()
    assert len(db.outliers_missing_analysis()) == 2

    class Cliente:
        def __init__(self):
            self.calls = 0

        def complete(self, system, user):
            self.calls += 1
            if self.calls == 2:
                raise CodexRateLimit("usage limit")
            return VALID

        def close(self):
            return None

    sleeps = []
    message = analyze_pending(
        db,
        settings,
        chat_client=Cliente(),
        sleeper=sleeps.append,
    )
    assert sleeps == [2]
    assert "límite" in message.lower() or "usage limit" in message
    pending = db.outliers_missing_analysis()
    assert len(pending) == 1
    assert pending[0]["id"] == ids[1]
    assert db.get_reel(ids[0])["analysis_error"] is None
    saved = db.get_reel(ids[1])
    assert saved["analysis_error"].startswith("Pendiente para la próxima tanda")
    assert json.loads(VALID)["hook_type"] == "otro"


def test_limite_de_uso_en_codex_no_reintenta(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path)
    calls = {"exec": 0}

    def fake_run(args, input=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        if args[1:3] == ["login", "status"]:
            return _result(0, "Logged in using ChatGPT\n")
        calls["exec"] += 1
        return _result(1, "", "usage limit reached")

    monkeypatch.setattr("radar.llm_codex.subprocess.run", fake_run)
    with pytest.raises(CodexRateLimit):
        check_backend(settings)
    assert calls["exec"] == 1


def test_en_windows_un_cmd_se_lanza_con_cmd_exe(monkeypatch, tmp_path):
    monkeypatch.setattr("radar.llm_codex.os.name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    cmd = tmp_path / "codex.CMD"
    cmd.write_text("@echo off\r\n", encoding="utf-8")
    vistos: list[str] = []

    def which(nombre: str) -> str:
        vistos.append(nombre)
        return str(cmd)

    monkeypatch.setattr("radar.llm_codex.shutil.which", which)
    argv = argv_ejecutable(["mi-codex", "exec", "--json", "-"])
    assert vistos == ["mi-codex"]
    assert argv[0] == r"C:\Windows\System32\cmd.exe"
    assert argv[1:4] == ["/d", "/s", "/c"]
    orden = argv[4]
    assert "codex.CMD" in orden
    assert "exec" in orden
    assert orden.rstrip().endswith("-")

    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr("radar.llm_codex.shutil.which", lambda _nombre: str(exe))
    directo = argv_ejecutable(["codex", "login", "status"])
    assert directo == [str(exe), "login", "status"]


def test_el_stdin_de_codex_sale_en_utf8_aunque_el_sistema_use_cp1252(monkeypatch, tmp_path):
    import locale

    monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale=True: "cp1252")
    destino = tmp_path / "recibido.bin"
    codigo = "import sys; open(sys.argv[1], 'wb').write(sys.stdin.buffer.read())"
    texto = "cañón — ¿sigues con la ñ?"
    resultado = _default_runner(
        [sys.executable, "-c", codigo, str(destino)],
        input_text=texto,
        timeout=30,
    )
    assert resultado.returncode == 0
    assert destino.read_bytes() == texto.encode("utf-8")


def test_con_clave_de_api_no_se_elige_codex(tmp_path, monkeypatch):
    settings = make_settings(monkeypatch, tmp_path, OPENAI_API_KEY="sk-test", LLM_MODEL="gpt-prueba")
    assert settings.llm_provider == "openai"
    explicito = make_settings(monkeypatch, tmp_path, RADAR_LLM="none", OPENAI_API_KEY="sk-test")
    assert explicito.llm_provider == "none"
    assert load_settings().llm_provider == "none"
