#!/usr/bin/env python3
"""
db.py — camada SQLite (única dependência: stdlib).

Tabelas:
  leads           fonte de verdade do CRM (1 linha por número E.164)
  touches         histórico de toques outbound (cada disparo grupo/x1/followup)
  inbox           mensagens recebidas (com intent classificado)
  conversations   estado de conversa (último toque, último inbound, status)
  followup_jobs   cadência multi-toque ativa por lead
  pipeline_events log de mudanças de estágio (auditoria)
  campaigns       metadados de campanha (broadcast/x1/ab)

Schema versionado em SCHEMA_VERSION. Migrações idempotentes.
"""

import pathlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "data.db"
SCHEMA_VERSION = 1


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS leads (
    phone_e164          TEXT PRIMARY KEY,
    phone_hash          TEXT NOT NULL,
    name                TEXT DEFAULT '',
    first_name          TEXT DEFAULT '',
    source              TEXT DEFAULT '',          -- grupo:<nome>, csv, manual, anuncio_meta
    status              TEXT NOT NULL DEFAULT 'novo',
    fit_score           INTEGER DEFAULT 0,         -- 0-100 (BANT)
    engagement_score    INTEGER DEFAULT 0,         -- 0-100 (toques + respostas)
    last_intent         TEXT DEFAULT '',
    last_outbound_at    TEXT,
    last_inbound_at     TEXT,
    touches_count       INTEGER DEFAULT 0,
    replies_count       INTEGER DEFAULT 0,
    notes               TEXT DEFAULT '',
    tags                TEXT DEFAULT '',           -- CSV interno: trial,quente,evento_15
    custom_fields       TEXT DEFAULT '{}',         -- JSON pra placeholders dinâmicos
    blacklisted         INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_status     ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_fit        ON leads(fit_score);
CREATE INDEX IF NOT EXISTS idx_leads_engagement ON leads(engagement_score);
CREATE INDEX IF NOT EXISTS idx_leads_last_out   ON leads(last_outbound_at);

CREATE TABLE IF NOT EXISTS touches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_e164      TEXT NOT NULL,
    channel         TEXT NOT NULL,                 -- grupo | x1 | followup | qualificacao
    campaign_id     TEXT,
    text            TEXT NOT NULL,
    media_type      TEXT,
    sent_at         TEXT NOT NULL,
    status          TEXT NOT NULL,                 -- ok | fail | retry_ok
    api_status      INTEGER,
    api_error       TEXT,
    FOREIGN KEY (phone_e164) REFERENCES leads(phone_e164)
);
CREATE INDEX IF NOT EXISTS idx_touches_phone    ON touches(phone_e164);
CREATE INDEX IF NOT EXISTS idx_touches_campaign ON touches(campaign_id);
CREATE INDEX IF NOT EXISTS idx_touches_sent     ON touches(sent_at);

CREATE TABLE IF NOT EXISTS inbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      TEXT UNIQUE,                   -- id da Zappfy (dedup)
    phone_e164      TEXT NOT NULL,
    text            TEXT NOT NULL,
    media_type      TEXT,
    received_at     TEXT NOT NULL,
    intent          TEXT DEFAULT 'desconhecido',
    intent_score    REAL DEFAULT 0,                -- 0-1 confidence
    handled         INTEGER DEFAULT 0,
    handled_action  TEXT DEFAULT '',
    raw             TEXT                           -- JSON cru da API (debug)
);
CREATE INDEX IF NOT EXISTS idx_inbox_phone    ON inbox(phone_e164);
CREATE INDEX IF NOT EXISTS idx_inbox_received ON inbox(received_at);
CREATE INDEX IF NOT EXISTS idx_inbox_intent   ON inbox(intent);
CREATE INDEX IF NOT EXISTS idx_inbox_handled  ON inbox(handled);

CREATE TABLE IF NOT EXISTS conversations (
    phone_e164         TEXT PRIMARY KEY,
    last_outbound_at   TEXT,
    last_inbound_at    TEXT,
    state              TEXT DEFAULT 'idle',        -- idle | aguardando_resposta | em_conversa | aguardando_reuniao
    last_state_change  TEXT,
    FOREIGN KEY (phone_e164) REFERENCES leads(phone_e164)
);

CREATE TABLE IF NOT EXISTS followup_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_e164      TEXT NOT NULL,
    cadence_name    TEXT NOT NULL,                 -- followup_padrao | recuperacao | pos_proposta
    step            INTEGER NOT NULL,              -- 0,1,2,3...
    fire_at         TEXT NOT NULL,                 -- ISO datetime
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | done | skipped | cancelled
    fired_at        TEXT,
    skip_reason     TEXT,
    FOREIGN KEY (phone_e164) REFERENCES leads(phone_e164)
);
CREATE INDEX IF NOT EXISTS idx_fj_status_fire ON followup_jobs(status, fire_at);
CREATE INDEX IF NOT EXISTS idx_fj_phone       ON followup_jobs(phone_e164);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_e164  TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT DEFAULT '',
    at          TEXT NOT NULL,
    FOREIGN KEY (phone_e164) REFERENCES leads(phone_e164)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    type         TEXT NOT NULL,                    -- broadcast | x1 | ab | followup
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    config       TEXT,                             -- JSON
    metrics      TEXT                              -- JSON (calculado pós)
);
"""


PIPELINE_STAGES = [
    "novo",
    "mql",          # Marketing Qualified Lead — engajou
    "sql",          # Sales Qualified Lead — fit ok + interesse declarado
    "em_conversa",
    "proposta",
    "negociacao",
    "ganho",
    "perdido",
]

PIPELINE_PROBABILITY = {
    "novo":         0.05,
    "mql":          0.10,
    "sql":          0.25,
    "em_conversa":  0.40,
    "proposta":     0.60,
    "negociacao":   0.75,
    "ganho":        1.00,
    "perdido":      0.00,
}


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


@contextmanager
def connect(db_path=None):
    path = pathlib.Path(db_path or DB_PATH)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    """Idempotente — pode chamar toda execução."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        if not row:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


def upsert_lead(conn, phone_e164, **fields):
    """Cria ou atualiza lead. Retorna lead atualizado."""
    import hashlib
    import json as _json

    now = now_iso()
    cur = conn.execute("SELECT * FROM leads WHERE phone_e164 = ?", (phone_e164,))
    existing = cur.fetchone()
    name = fields.get("name", "")
    first_name = (name.split()[0] if name else "") or fields.get("first_name", "")

    if existing is None:
        phash = hashlib.sha256(phone_e164.encode("utf-8")).hexdigest()[:16]
        conn.execute(
            """INSERT INTO leads
               (phone_e164, phone_hash, name, first_name, source, status,
                fit_score, engagement_score, last_intent, notes, tags, custom_fields,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'novo', 0, 0, '', ?, ?, ?, ?, ?)""",
            (
                phone_e164, phash,
                name or "",
                first_name or "",
                fields.get("source", ""),
                fields.get("notes", ""),
                fields.get("tags", ""),
                _json.dumps(fields.get("custom_fields", {})),
                now, now,
            ),
        )
    else:
        sets = []
        vals = []
        for col in ("name", "first_name", "source", "status", "fit_score",
                    "engagement_score", "last_intent", "last_outbound_at",
                    "last_inbound_at", "touches_count", "replies_count",
                    "notes", "tags", "blacklisted"):
            if col in fields:
                sets.append(f"{col} = ?")
                vals.append(fields[col])
        # nome chegou depois (lead criado sem nome): preenche o primeiro nome junto
        if fields.get("name") and "first_name" not in fields:
            sets.append("first_name = ?")
            vals.append(first_name)
        if "custom_fields" in fields:
            sets.append("custom_fields = ?")
            vals.append(_json.dumps(fields["custom_fields"]))
        sets.append("updated_at = ?"); vals.append(now)
        vals.append(phone_e164)
        if sets:
            conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE phone_e164 = ?", vals)

    return conn.execute("SELECT * FROM leads WHERE phone_e164 = ?", (phone_e164,)).fetchone()


def log_touch(conn, phone_e164, channel, text, status, api_status=None,
              api_error=None, campaign_id=None, media_type=None):
    now = now_iso()
    conn.execute(
        """INSERT INTO touches
           (phone_e164, channel, campaign_id, text, media_type, sent_at,
            status, api_status, api_error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (phone_e164, channel, campaign_id, text, media_type, now,
         status, api_status, api_error),
    )
    conn.execute(
        """UPDATE leads SET
             last_outbound_at = ?,
             touches_count    = touches_count + 1,
             updated_at       = ?
           WHERE phone_e164 = ?""",
        (now, now, phone_e164),
    )
    cur = conn.execute("SELECT 1 FROM conversations WHERE phone_e164 = ?", (phone_e164,))
    if not cur.fetchone():
        conn.execute(
            "INSERT INTO conversations (phone_e164, last_outbound_at, state, last_state_change) VALUES (?, ?, 'aguardando_resposta', ?)",
            (phone_e164, now, now),
        )
    else:
        conn.execute(
            "UPDATE conversations SET last_outbound_at = ?, state = 'aguardando_resposta' WHERE phone_e164 = ?",
            (now, phone_e164),
        )


def log_inbound(conn, message_id, phone_e164, text, intent, intent_score,
                received_at=None, media_type=None, raw=None):
    received_at = received_at or now_iso()
    try:
        conn.execute(
            """INSERT INTO inbox
               (message_id, phone_e164, text, media_type, received_at, intent, intent_score, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, phone_e164, text, media_type, received_at, intent, intent_score, raw),
        )
    except sqlite3.IntegrityError:
        return False  # já existe (dedup)

    conn.execute(
        """UPDATE leads SET
             last_inbound_at = ?,
             last_intent     = ?,
             replies_count   = replies_count + 1,
             engagement_score = MIN(100, engagement_score + 15),
             updated_at      = ?
           WHERE phone_e164 = ?""",
        (received_at, intent, received_at, phone_e164),
    )
    cur = conn.execute("SELECT 1 FROM conversations WHERE phone_e164 = ?", (phone_e164,))
    if cur.fetchone():
        conn.execute(
            "UPDATE conversations SET last_inbound_at = ?, state = 'em_conversa' WHERE phone_e164 = ?",
            (received_at, phone_e164),
        )
    else:
        conn.execute(
            "INSERT INTO conversations (phone_e164, last_inbound_at, state, last_state_change) VALUES (?, ?, 'em_conversa', ?)",
            (phone_e164, received_at, received_at),
        )
    return True


def change_status(conn, phone_e164, new_status, reason=""):
    cur = conn.execute("SELECT status FROM leads WHERE phone_e164 = ?", (phone_e164,))
    row = cur.fetchone()
    if not row:
        return False
    if new_status not in PIPELINE_STAGES:
        raise ValueError(f"status inválido: {new_status}")
    old = row["status"]
    if old == new_status:
        return False
    now = now_iso()
    conn.execute("UPDATE leads SET status = ?, updated_at = ? WHERE phone_e164 = ?",
                 (new_status, now, phone_e164))
    conn.execute(
        "INSERT INTO pipeline_events (phone_e164, from_status, to_status, reason, at) VALUES (?, ?, ?, ?, ?)",
        (phone_e164, old, new_status, reason, now),
    )
    return True


def add_to_blacklist_db(conn, phone_e164, reason=""):
    now = now_iso()
    conn.execute(
        "UPDATE leads SET blacklisted = 1, status = 'perdido', notes = notes || ?, updated_at = ? WHERE phone_e164 = ?",
        (f"\n[blacklist {now}] {reason}", now, phone_e164),
    )
    cur = conn.execute("SELECT status FROM leads WHERE phone_e164 = ?", (phone_e164,))
    row = cur.fetchone()
    if row and row["status"] != "perdido":
        change_status(conn, phone_e164, "perdido", f"opt-out: {reason}")
