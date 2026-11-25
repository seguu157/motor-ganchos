"""SQLite como única fuente de verdad.

La UI y el CLI pasan por esta clase. Más adelante se puede sustituir por un
almacén remoto con los mismos métodos; el JSON de exportación es el puente
para copiar los datos entre el móvil y el ordenador mientras tanto.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar.categorias import categoria_de
from radar.outliers import (
    DEFAULT_COMMENT_WEIGHT,
    DEFAULT_SAMPLE_SIZE,
    DEFAULT_THRESHOLD,
    PlaySample,
    detect_outliers,
    engagement_score,
)
from radar.perfil import CAMPOS, Perfil, perfil_desde_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    reels_requested INTEGER,
    reels_saved INTEGER,
    estimated_usd TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS reels (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id),
    instagram_id TEXT NOT NULL UNIQUE,
    short_code TEXT,
    url TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    likes_count INTEGER,
    comments_count INTEGER,
    video_play_count INTEGER,
    video_view_count INTEGER,
    video_url TEXT,
    audio_url TEXT,
    video_duration REAL,
    is_example INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT,
    median_plays REAL,
    multiple REAL,
    is_outlier INTEGER,
    sample_size INTEGER,
    fetched_at TEXT NOT NULL,
    transcript_error TEXT,
    analysis_error TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    reel_id INTEGER PRIMARY KEY REFERENCES reels(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    language TEXT,
    hook_text TEXT NOT NULL DEFAULT '',
    segments_json TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    reel_id INTEGER PRIMARY KEY REFERENCES reels(id) ON DELETE CASCADE,
    hook_type TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    template_text TEXT NOT NULL,
    adapted_script TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    provider TEXT,
    model TEXT,
    raw_response TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS perfil (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    marca TEXT NOT NULL DEFAULT '',
    oferta TEXT NOT NULL DEFAULT '',
    audiencia TEXT NOT NULL DEFAULT '',
    problema TEXT NOT NULL DEFAULT '',
    tono TEXT NOT NULL DEFAULT '',
    idioma TEXT NOT NULL DEFAULT 'es',
    pruebas TEXT NOT NULL DEFAULT '',
    restricciones TEXT NOT NULL DEFAULT '',
    cta TEXT NOT NULL DEFAULT '',
    nicho TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS proyectos (
    id INTEGER PRIMARY KEY,
    titulo TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'borrador',
    paso TEXT NOT NULL DEFAULT 'oferta',
    perfil_json TEXT NOT NULL DEFAULT '{}',
    referencias_json TEXT NOT NULL DEFAULT '[]',
    hook_reel_id INTEGER,
    guion TEXT NOT NULL DEFAULT '',
    variantes_json TEXT NOT NULL DEFAULT '[]',
    notas TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tt_cuentas (
    id INTEGER PRIMARY KEY,
    handle TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL DEFAULT '',
    nombre TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    enlace_bio TEXT NOT NULL DEFAULT '',
    que_vende TEXT NOT NULL DEFAULT '',
    tipo_producto TEXT NOT NULL DEFAULT '',
    nicho TEXT NOT NULL DEFAULT '',
    subnicho TEXT NOT NULL DEFAULT '',
    idioma TEXT NOT NULL DEFAULT '',
    seguidores INTEGER,
    posts_muestreados INTEGER,
    fijados INTEGER,
    mediana_vistas INTEGER,
    slideshows_muestra TEXT NOT NULL DEFAULT '',
    es_cuenta_slideshow TEXT NOT NULL DEFAULT '',
    num_piezas_virales INTEGER,
    num_virales_no_fijadas INTEGER,
    mejor_reciente_url TEXT NOT NULL DEFAULT '',
    mejor_reciente_vistas INTEGER,
    mejor_reciente_multiplo REAL,
    mejor_pieza_url TEXT NOT NULL DEFAULT '',
    mejor_pieza_vistas INTEGER,
    mejor_pieza_multiplo REAL,
    mejor_pieza_slideshow TEXT NOT NULL DEFAULT '',
    mejor_pieza_fijada TEXT NOT NULL DEFAULT '',
    piezas_virales TEXT NOT NULL DEFAULT '',
    ultima_publicacion TEXT NOT NULL DEFAULT '',
    clasificacion TEXT NOT NULL DEFAULT '',
    fecha_detectada TEXT NOT NULL DEFAULT '',
    fecha_actualizada TEXT NOT NULL DEFAULT '',
    fuente TEXT NOT NULL DEFAULT '',
    semilla TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'candidata',
    motivo TEXT NOT NULL DEFAULT '',
    captions_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tt_semillas (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    valor TEXT NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1,
    notas TEXT NOT NULL DEFAULT '',
    leida_en TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(tipo, valor)
);

CREATE TABLE IF NOT EXISTS tt_pasadas (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    leidas INTEGER NOT NULL DEFAULT 0,
    nuevas INTEGER NOT NULL DEFAULT 0,
    califican INTEGER NOT NULL DEFAULT 0,
    descartadas INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tt_cuentas_estado ON tt_cuentas(estado);
CREATE INDEX IF NOT EXISTS idx_tt_cuentas_nicho ON tt_cuentas(nicho);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class BankEntry:
    reel_id: int
    account: str
    url: str
    caption: str
    published_at: str | None
    likes_count: int | None
    comments_count: int | None
    play_count: int | None
    median_plays: float | None
    multiple: float | None
    is_outlier: bool
    is_example: bool
    sample_size: int | None
    short_code: str | None
    hook: str | None
    transcript: str | None
    hook_type: str | None
    blocks: list[dict[str, str]] = field(default_factory=list)
    template: str | None = None
    adapted_script: str | None = None
    warnings: list[str] = field(default_factory=list)
    transcript_error: str | None = None
    analysis_error: str | None = None
    video_duration: float | None = None
    media_kind: str = "reel"
    slides: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    engagement_score: float | None = None
    comment_weight: float | None = None
    categoria: str = ""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            _ensure_column(conn, "reels", "media_kind", "TEXT NOT NULL DEFAULT 'reel'")
            _ensure_column(conn, "reels", "slides_json", "TEXT")
            _ensure_column(conn, "reels", "engagement_score", "REAL")
            _ensure_column(conn, "reels", "comment_weight", "REAL")

    def get_meta(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def count_accounts(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"])

    def list_accounts(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM accounts ORDER BY username").fetchall()

    def list_active_usernames(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT username FROM accounts WHERE active = 1 ORDER BY username"
            ).fetchall()
        return [row["username"] for row in rows]

    def ensure_account(self, username: str, *, active: bool = True) -> int:
        """Crea la cuenta si no existe. Si ya existe, no cambia su estado activo."""
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE username = ?", (username,)).fetchone()
        if row is not None:
            return int(row["id"])
        return self.upsert_account(username, active=active)

    def upsert_account(self, username: str, *, active: bool = True, notes: str = "") -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts(username, active, notes, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    active = excluded.active,
                    notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE accounts.notes END
                """,
                (username, 1 if active else 0, notes, now),
            )
            row = conn.execute("SELECT id FROM accounts WHERE username = ?", (username,)).fetchone()
        return int(row["id"])

    def set_account_active(self, username: str, active: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE accounts SET active = ? WHERE username = ?", (1 if active else 0, username))

    def delete_account(self, username: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM accounts WHERE username = ?", (username,))

    def upsert_reel(self, account_id: int, reel: Any, *, is_example: bool, run_id: int | None) -> int:
        from radar.carrusel import fusionar_diapositivas

        now = utc_now()
        raw = json.dumps(reel.raw, ensure_ascii=False)
        media_kind = getattr(reel, "media_kind", None) or "reel"
        entrantes = list(getattr(reel, "slides", ()) or ())
        with self.connect() as conn:
            previa = conn.execute(
                "SELECT slides_json FROM reels WHERE instagram_id = ?",
                (reel.instagram_id,),
            ).fetchone()
        guardadas = json.loads(previa["slides_json"]) if previa and previa["slides_json"] else []
        if not isinstance(guardadas, list):
            guardadas = []
        slides = fusionar_diapositivas(guardadas, entrantes, media_kind=media_kind)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reels (
                    account_id, run_id, instagram_id, short_code, url, caption, published_at,
                    likes_count, comments_count, video_play_count, video_view_count,
                    video_url, audio_url, video_duration, is_example, raw_json, fetched_at,
                    media_kind, slides_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instagram_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    run_id = COALESCE(excluded.run_id, reels.run_id),
                    short_code = excluded.short_code,
                    url = excluded.url,
                    caption = excluded.caption,
                    published_at = excluded.published_at,
                    likes_count = excluded.likes_count,
                    comments_count = excluded.comments_count,
                    video_play_count = excluded.video_play_count,
                    video_view_count = excluded.video_view_count,
                    video_url = excluded.video_url,
                    audio_url = excluded.audio_url,
                    video_duration = excluded.video_duration,
                    is_example = excluded.is_example,
                    raw_json = excluded.raw_json,
                    fetched_at = excluded.fetched_at,
                    media_kind = excluded.media_kind,
                    slides_json = excluded.slides_json
                """,
                (
                    account_id,
                    run_id,
                    reel.instagram_id,
                    reel.short_code,
                    reel.url,
                    reel.caption,
                    reel.published_at,
                    reel.likes_count,
                    reel.comments_count,
                    reel.video_play_count,
                    reel.video_view_count,
                    reel.video_url,
                    reel.audio_url,
                    reel.video_duration,
                    1 if is_example else 0,
                    raw,
                    now,
                    media_kind,
                    json.dumps(slides, ensure_ascii=False),
                ),
            )
            row = conn.execute("SELECT id FROM reels WHERE instagram_id = ?", (reel.instagram_id,)).fetchone()
            reel_id = int(row["id"])
            if _firma_diapositivas(guardadas) != _firma_diapositivas(slides):
                conn.execute("DELETE FROM transcripts WHERE reel_id = ?", (reel_id,))
        return reel_id

    def list_reels(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT reels.*, accounts.username AS account
                FROM reels JOIN accounts ON accounts.id = reels.account_id
                ORDER BY COALESCE(reels.published_at, '') DESC, reels.id DESC
                """
            ).fetchall()

    def get_reel(self, reel_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT reels.*, accounts.username AS account
                FROM reels JOIN accounts ON accounts.id = reels.account_id
                WHERE reels.id = ?
                """,
                (reel_id,),
            ).fetchone()

    def recompute_outliers(
        self,
        *,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        threshold: float = DEFAULT_THRESHOLD,
        comment_weight: float = DEFAULT_COMMENT_WEIGHT,
    ) -> int:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT reels.id, reels.instagram_id, reels.video_play_count, reels.published_at,
                       reels.likes_count, reels.comments_count, reels.media_kind,
                       accounts.username AS account
                FROM reels JOIN accounts ON accounts.id = reels.account_id
                """
            ).fetchall()
        reel_samples: list[PlaySample] = []
        carousel_samples: list[PlaySample] = []
        engagement: dict[str, int | None] = {}
        for row in rows:
            kind = row["media_kind"] or "reel"
            if kind == "carrusel":
                score = engagement_score(row["likes_count"], row["comments_count"], weight=comment_weight)
                engagement[row["instagram_id"]] = score
                carousel_samples.append(
                    PlaySample(
                        reel_key=row["instagram_id"],
                        account=row["account"],
                        play_count=score,
                        published_at=row["published_at"],
                    )
                )
            else:
                reel_samples.append(
                    PlaySample(
                        reel_key=row["instagram_id"],
                        account=row["account"],
                        play_count=row["video_play_count"],
                        published_at=row["published_at"],
                    )
                )
        scores = {
            score.reel_key: score
            for score in detect_outliers(reel_samples, sample_size=sample_size, threshold=threshold)
        }
        scores.update(
            {
                score.reel_key: score
                for score in detect_outliers(carousel_samples, sample_size=sample_size, threshold=threshold)
            }
        )
        with self.connect() as conn:
            for row in rows:
                kind = row["media_kind"] or "reel"
                score = scores.get(row["instagram_id"])
                points = engagement.get(row["instagram_id"]) if kind == "carrusel" else None
                weight = comment_weight if kind == "carrusel" else None
                if score is None:
                    conn.execute(
                        """
                        UPDATE reels
                        SET median_plays = NULL, multiple = NULL, is_outlier = 0, sample_size = NULL,
                            engagement_score = ?, comment_weight = ?
                        WHERE id = ?
                        """,
                        (points, weight, row["id"]),
                    )
                else:
                    multiple = None if score.multiple is None else float(score.multiple)
                    conn.execute(
                        """
                        UPDATE reels
                        SET median_plays = ?, multiple = ?, is_outlier = ?, sample_size = ?,
                            engagement_score = ?, comment_weight = ?
                        WHERE id = ?
                        """,
                        (
                            score.median_plays,
                            multiple,
                            1 if score.is_outlier else 0,
                            score.sample_size,
                            points,
                            weight,
                            row["id"],
                        ),
                    )
        return sum(1 for score in scores.values() if score.is_outlier)

    def save_transcript(
        self,
        reel_id: int,
        *,
        text: str,
        language: str | None,
        hook_text: str,
        segments: list[dict[str, Any]] | None,
        model: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO transcripts(reel_id, text, language, hook_text, segments_json, model, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reel_id) DO UPDATE SET
                    text = excluded.text,
                    language = excluded.language,
                    hook_text = excluded.hook_text,
                    segments_json = excluded.segments_json,
                    model = excluded.model,
                    created_at = excluded.created_at
                """,
                (reel_id, text, language, hook_text, json.dumps(segments or [], ensure_ascii=False), model, utc_now()),
            )
            conn.execute("UPDATE reels SET transcript_error = NULL WHERE id = ?", (reel_id,))

    def set_transcript_error(self, reel_id: int, message: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE reels SET transcript_error = ? WHERE id = ?", (message, reel_id))

    def save_analysis(
        self,
        reel_id: int,
        *,
        hook_type: str,
        blocks: list[dict[str, str]],
        template: str,
        adapted_script: str,
        warnings: list[str],
        provider: str | None,
        model: str | None,
        raw: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analyses(
                    reel_id, hook_type, blocks_json, template_text, adapted_script,
                    warnings_json, provider, model, raw_response, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reel_id) DO UPDATE SET
                    hook_type = excluded.hook_type,
                    blocks_json = excluded.blocks_json,
                    template_text = excluded.template_text,
                    adapted_script = excluded.adapted_script,
                    warnings_json = excluded.warnings_json,
                    provider = excluded.provider,
                    model = excluded.model,
                    raw_response = excluded.raw_response,
                    created_at = excluded.created_at
                """,
                (
                    reel_id,
                    hook_type,
                    json.dumps(blocks, ensure_ascii=False),
                    template,
                    adapted_script,
                    json.dumps(warnings, ensure_ascii=False),
                    provider,
                    model,
                    raw,
                    utc_now(),
                ),
            )
            conn.execute("UPDATE reels SET analysis_error = NULL WHERE id = ?", (reel_id,))

    def set_analysis_error(self, reel_id: int, message: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE reels SET analysis_error = ? WHERE id = ?", (message, reel_id))

    def list_bank(self) -> list[BankEntry]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT reels.*, accounts.username AS account,
                       transcripts.text AS transcript_text,
                       transcripts.hook_text AS hook_text,
                       transcripts.segments_json AS segments_json,
                       analyses.hook_type AS hook_type,
                       analyses.blocks_json AS blocks_json,
                       analyses.template_text AS template_text,
                       analyses.adapted_script AS adapted_script,
                       analyses.warnings_json AS warnings_json
                FROM reels
                JOIN accounts ON accounts.id = reels.account_id
                LEFT JOIN transcripts ON transcripts.reel_id = reels.id
                LEFT JOIN analyses ON analyses.reel_id = reels.id
                WHERE reels.is_outlier = 1
                ORDER BY COALESCE(reels.multiple, 0) DESC, accounts.username
                """
            ).fetchall()
        return [self._bank_entry(row) for row in rows]

    def get_bank_entry(self, reel_id: int) -> BankEntry | None:
        for entry in self.list_bank():
            if entry.reel_id == reel_id:
                return entry
        return None

    def update_slides(self, reel_id: int, slides: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE reels SET slides_json = ? WHERE id = ?",
                (json.dumps(slides, ensure_ascii=False), reel_id),
            )

    def carruseles_sin_transcripcion(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT reels.*, accounts.username AS account
                FROM reels
                JOIN accounts ON accounts.id = reels.account_id
                LEFT JOIN transcripts ON transcripts.reel_id = reels.id
                WHERE reels.media_kind = 'carrusel'
                  AND reels.is_outlier = 1
                  AND transcripts.reel_id IS NULL
                ORDER BY reels.id
                """
            ).fetchall()

    def get_perfil(self) -> Perfil:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM perfil WHERE id = 1").fetchone()
        if row is None:
            return Perfil()
        return perfil_desde_dict({campo: row[campo] for campo in CAMPOS})

    def save_perfil(self, perfil: Perfil) -> None:
        datos = perfil.as_dict()
        columnas = ", ".join(CAMPOS)
        placeholders = ", ".join("?" for _ in CAMPOS)
        updates = ", ".join(f"{campo} = excluded.{campo}" for campo in CAMPOS)
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO perfil(id, {columnas})
                VALUES(1, {placeholders})
                ON CONFLICT(id) DO UPDATE SET {updates}
                """,
                tuple(datos[campo] for campo in CAMPOS),
            )

    def create_proyecto(self, titulo: str, *, perfil_json: str) -> int:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO proyectos(
                    titulo, estado, paso, perfil_json, referencias_json, guion,
                    variantes_json, notas, created_at, updated_at
                ) VALUES(?, 'borrador', 'oferta', ?, '[]', '', '[]', '', ?, ?)
                """,
                (titulo, perfil_json, now, now),
            )
            return int(cursor.lastrowid)

    def list_proyectos(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM proyectos ORDER BY id DESC").fetchall()

    def get_proyecto(self, proyecto_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM proyectos WHERE id = ?", (proyecto_id,)).fetchone()

    def update_proyecto(self, proyecto_id: int, **fields: Any) -> None:
        allowed = {
            "titulo",
            "estado",
            "paso",
            "perfil_json",
            "referencias_json",
            "hook_reel_id",
            "guion",
            "variantes_json",
            "notas",
        }
        assignments = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Campo de proyecto no permitido: {key}")
            assignments.append(f"{key} = ?")
            values.append(value)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(proyecto_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE proyectos SET {', '.join(assignments)} WHERE id = ?", values)

    def outliers_missing_transcript(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT reels.*, accounts.username AS account
                FROM reels
                JOIN accounts ON accounts.id = reels.account_id
                LEFT JOIN transcripts ON transcripts.reel_id = reels.id
                WHERE reels.is_outlier = 1
                  AND transcripts.reel_id IS NULL
                  AND COALESCE(reels.media_kind, 'reel') != 'carrusel'
                ORDER BY reels.id
                """
            ).fetchall()

    def outliers_missing_analysis(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT reels.*, accounts.username AS account, transcripts.text AS transcript_text
                FROM reels
                JOIN accounts ON accounts.id = reels.account_id
                JOIN transcripts ON transcripts.reel_id = reels.id
                LEFT JOIN analyses ON analyses.reel_id = reels.id
                WHERE reels.is_outlier = 1 AND analyses.reel_id IS NULL
                ORDER BY reels.id
                """
            ).fetchall()

    def create_run(self, *, reels_requested: int, estimated_usd: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs(started_at, status, reels_requested, estimated_usd)
                VALUES(?, 'running', ?, ?)
                """,
                (utc_now(), reels_requested, estimated_usd),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, status: str, reels_saved: int, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = ?, reels_saved = ?, message = ?
                WHERE id = ?
                """,
                (utc_now(), status, reels_saved, message, run_id),
            )

    def list_runs(self, limit: int = 8) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def running_run(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
            ).fetchone()

    @staticmethod
    def _bank_entry(row: sqlite3.Row) -> BankEntry:
        blocks = json.loads(row["blocks_json"]) if row["blocks_json"] else []
        warnings = json.loads(row["warnings_json"]) if row["warnings_json"] else []
        slides = json.loads(row["slides_json"]) if row["slides_json"] else []
        segments = json.loads(row["segments_json"]) if row["segments_json"] else []
        media_kind = row["media_kind"] or "reel"
        return BankEntry(
            reel_id=row["id"],
            account=row["account"],
            url=row["url"],
            caption=row["caption"],
            published_at=row["published_at"],
            likes_count=row["likes_count"],
            comments_count=row["comments_count"],
            play_count=row["video_play_count"],
            median_plays=row["median_plays"],
            multiple=row["multiple"],
            is_outlier=bool(row["is_outlier"]),
            is_example=bool(row["is_example"]),
            sample_size=row["sample_size"],
            short_code=row["short_code"],
            hook=row["hook_text"],
            transcript=row["transcript_text"],
            hook_type=row["hook_type"],
            blocks=blocks,
            template=row["template_text"],
            adapted_script=row["adapted_script"],
            warnings=warnings,
            transcript_error=row["transcript_error"],
            analysis_error=row["analysis_error"],
            video_duration=row["video_duration"],
            media_kind=media_kind,
            slides=slides if isinstance(slides, list) else [],
            segments=segments if isinstance(segments, list) else [],
            engagement_score=row["engagement_score"],
            comment_weight=row["comment_weight"],
            categoria=categoria_de(row["hook_type"], media_kind),
        )


def _firma_diapositivas(slides: list[dict[str, Any]]) -> str:
    partes = []
    for posicion, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue
        partes.append(f"{slide.get('indice') or posicion}:{str(slide.get('texto') or '').strip()}")
    return "\n".join(partes)


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
