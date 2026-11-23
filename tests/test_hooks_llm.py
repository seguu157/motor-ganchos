import json

import httpx

from radar.hooks import Segment, extract_hook, first_sentence
from radar.llm import AnthropicClient, OpenAICompatibleClient, parse_analysis
from radar.numbers import unexpected_numbers
from radar.transcribe import WhisperNotInstalled, choose_media_url, transcribe_file
from radar.tubealfred import TubeAlfredAdapter, TubeAlfredDisabled


def test_gancho_es_la_primera_frase_o_los_tres_primeros_segundos():
    text = "¿Sigues abriendo cinco pestañas? Yo lo hacía cada lunes."
    assert first_sentence(text) == "¿Sigues abriendo cinco pestañas?"
    assert extract_hook(text) == "¿Sigues abriendo cinco pestañas?"

    segments = [
        Segment(0.0, 1.4, "Mira esto"),
        Segment(1.4, 2.6, "antes de seguir"),
        Segment(3.4, 6.0, "porque el resto es contexto."),
    ]
    full = "Mira esto antes de seguir porque el resto es contexto."
    assert extract_hook(full, segments) == "Mira esto antes de seguir"

    covered = [
        Segment(0.0, 2.4, "¿Sigues abriendo cinco pestañas?"),
        Segment(3.2, 5.0, "Yo lo hacía cada lunes."),
    ]
    assert extract_hook(text, covered) == "¿Sigues abriendo cinco pestañas?"


def test_el_analisis_no_se_queda_cifras_que_no_estan_en_la_fuente():
    raw = """```json
    {
      "hook_type": "pregunta",
      "blocks": [{"id": "gancho", "texto": "¿Sigues [X]?", "en_pantalla": "[X]"}],
      "template": "Creció un 300% en 2024",
      "adapted_script": "En 15 días monté [X]"
    }
    ```"""
    draft = parse_analysis(raw, source="¿Sigues abriendo cinco pestañas? Tardé 15 días.")
    assert draft.hook_type == "pregunta"
    assert [block["id"] for block in draft.blocks] == ["gancho", "contexto", "conflicto", "giro", "cierre"]
    assert draft.blocks[1]["texto"] == "[X]"
    assert any("300" in warning for warning in draft.warnings)
    assert any("2024" in warning for warning in draft.warnings)
    assert not any("15" == warning for warning in unexpected_numbers("Tardé 15 días.", draft.adapted_script))
    assert unexpected_numbers("Tardé 15 días.", draft.adapted_script) == []
    assert "300" in unexpected_numbers("sin cifras", "un 300%")


def test_clientes_llm_no_salen_a_la_red():
    def openai_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer test"
        body = json.loads(request.content)
        assert body["model"] == "modelo-de-prueba"
        assert "No inventes cifras" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"hook_type\": \"otro\"}"}}]})

    client = OpenAICompatibleClient(
        api_key="test",
        base_url="https://example.invalid/v1",
        model="modelo-de-prueba",
        transport=httpx.MockTransport(openai_handler),
    )
    assert "hook_type" in client.complete("No inventes cifras.", "hola")
    client.close()

    def anthropic_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{\"hook_type\": \"lista\"}"}]})

    anthropic = AnthropicClient(api_key="test", model="modelo", transport=httpx.MockTransport(anthropic_handler))
    assert "lista" in anthropic.complete("sistema", "usuario")
    anthropic.close()


def test_tubealfred_esta_apagado_aunque_haya_clave():
    import pytest

    adapter = TubeAlfredAdapter("clave-de-ejemplo")
    assert adapter.enabled is False
    assert adapter.key_configured is True
    with pytest.raises(TubeAlfredDisabled):
        adapter.fetch_recent("un-canal")


def test_whisper_no_esta_instalado_y_la_url_tiene_que_ser_http(tmp_path):
    assert choose_media_url("https://example.invalid/a.mp4", None).endswith("a.mp4")
    assert choose_media_url(None, "https://example.invalid/v.mp4").endswith("v.mp4")
    assert choose_media_url("file:///tmp/a.mp4", None) is None
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        import pytest

        with pytest.raises(WhisperNotInstalled):
            transcribe_file(tmp_path / "no-existe.wav")
    else:
        import pytest

        pytest.skip("faster-whisper está instalado; no hace falta el aviso")
