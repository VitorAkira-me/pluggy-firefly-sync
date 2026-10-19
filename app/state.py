"""Estado de automação em SQLite (volume /data). NÃO é livro-razão.

Guarda só o que não pertence ao Firefly e precisa sobreviver a um redeploy:
valores de fatura informados pelo bot, offset do Telegram, cache de dados de
mercado, histórico do que o gestor já disse e das aulas já dadas. Saldo,
transação e categoria continuam só no Firefly (CLAUDE.md regra 1).

Conceito: SQLite é um banco inteiro num arquivo, sem servidor. Com `WAL`
(write-ahead log), um processo pode ler enquanto outro escreve, o que importa
aqui porque o bot e os jobs agendados rodam ao mesmo tempo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = Path(os.environ.get("PF_DATA_DIR") or ("/data" if Path("/data").is_dir() else ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (          -- diário do gestor: o que foi dito, decidido, ensinado
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,                      -- insight | aula | decisao | fatura | gasto_manual | revisao
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_kind_at ON events(kind, at);
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect():
    con = sqlite3.connect(data_dir() / "state.sqlite", timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def get(key: str, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key: str, value) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _now()),
        )


def log_event(kind: str, payload: dict) -> None:
    with connect() as con:
        con.execute("INSERT INTO events(at, kind, payload) VALUES(?, ?, ?)",
                    (_now(), kind, json.dumps(payload, ensure_ascii=False)))


def recent_events(kind: str, limit: int = 10) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT at, payload FROM events WHERE kind = ? ORDER BY id DESC LIMIT ?",
                           (kind, limit)).fetchall()
    return [{"at": at, **json.loads(p)} for at, p in rows]


# ------------------------------------------------------------- faturas manuais
def set_manual_invoice(card_name: str, amount: float, day: int | None = None, my_share: bool = False) -> dict:
    invoices = get("manual_invoices", {}) or {}
    entry = {"amount": round(float(amount), 2), "updated_at": datetime.now(UTC).date().isoformat()}
    if my_share:
        entry["minha_parte"] = True
    if day:
        entry["day"] = int(day)
    elif card_name in invoices and invoices[card_name].get("day"):
        entry["day"] = invoices[card_name]["day"]
    invoices[card_name] = entry
    put("manual_invoices", invoices)
    log_event("fatura", {"cartao": card_name, **entry})
    return entry


def manual_invoices() -> dict:
    return get("manual_invoices", {}) or {}


# ------------------------------------------------------------- trava da IA
@contextmanager
def llm_lock(timeout: float = 0.0):
    """Uma chamada de LLM por vez entre todos os processos deste container (bot e jobs).

    Trava por arquivo com O_EXCL: quem cria o arquivo primeiro tem a vez. Arquivo
    mais velho que 10 min é considerado abandonado (processo morto) e é removido.
    Rende True se conseguiu a vez, False se desistiu (`timeout` em segundos)."""
    path = data_dir() / "llm.lock"
    deadline = time.monotonic() + timeout
    got = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            got = True
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 600:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
    try:
        yield got
    finally:
        if got:
            path.unlink(missing_ok=True)
