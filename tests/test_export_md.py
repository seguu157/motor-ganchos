import json

from radar.db import Database
from radar.export_json import export_document, import_document
from radar.export_md import parse_markdown, render_markdown
from radar.seed import seed_examples


def test_markdown_de_los_ejemplos_y_reimportacion(tmp_path):
    db = Database(tmp_path / "radar.sqlite")
    seed_examples(db)
    bank = db.list_bank()
    markdown = render_markdown(bank)

    assert markdown.startswith("# Banco de ganchos\n")
    assert "10,5x · @jordigpt · 2026-11-18" in markdown
    assert "https://www.instagram.com/reel/EJEMPLOJG4/" in markdown
    assert "### Gancho" in markdown
    assert "### Transcripción" in markdown
    assert "### Formato" in markdown
    assert "### Plantilla" in markdown
    assert "### Guion adaptado" in markdown
    assert "¿Sigues abriendo cinco pestañas para un solo informe?" in markdown
    assert "[X]" in markdown
    assert "- **Ejemplo:** sí" in markdown
    assert "5,0x · @dani_llamazares" in markdown
    assert "3,2x · @gregisenberg" in markdown
    assert "2,0x · @crawlingmondays" in markdown
    assert "12345" not in markdown
    assert "12.345" not in markdown
    for name in ("Gancho", "Contexto", "Conflicto", "Giro", "Cierre"):
        assert f"**{name}:**" in markdown

    parsed = parse_markdown(markdown)
    first = parsed[0]
    assert first["multiple_label"] == "10,5x"
    assert first["account"] == "jordigpt"
    assert first["URL"] == "https://www.instagram.com/reel/EJEMPLOJG4/"
    assert first["Gancho"].startswith("¿Sigues abriendo")
    assert "[X]" in first["Guion adaptado"]
    assert len(parsed) == 4

    other = Database(tmp_path / "copia.sqlite")
    document = export_document(db)
    saved = import_document(other, document)
    assert saved == len(document["reels"])
    again = render_markdown(other.list_bank())
    assert "10,5x · @jordigpt · 2026-11-18" in again
    raw = json.loads(json.dumps(document))
    hook = next(item for item in raw["hooks"] if item["account"] == "jordigpt")
    assert hook["play_count"] == 21000
    assert hook["multiple"] == 10.5
    assert hook["multiple_label"] == "10,5x"
    assert hook["is_example"] is True
