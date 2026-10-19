"""Configuração: variáveis de ambiente + arquivos JSON de config/.

Tudo que é segredo vem do ambiente (Portainer). Tudo que é regra editável
(mapeamento de contas, regras de categoria, limiares de preço) vem de config/.
Os JSON são lidos uma vez por processo (lru_cache), não a cada transação.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("PF_CONFIG_DIR", ROOT / "config"))

try:  # .env só existe em execução local; no container as variáveis vêm do Docker
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    return float(raw) if raw.strip() else default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.strip() else default


@dataclass(frozen=True)
class Settings:
    pluggy_client_id: str
    pluggy_client_secret: str
    firefly_base_url: str
    firefly_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    monthly_savings_target: float
    safety_buffer: float
    report_hour_utc: int
    review_hour_utc: int
    insights_weekday_utc: int
    insights_hour_utc: int
    sync_every_hours: int
    sync_since_days: int
    llm_base_url: str
    llm_model: str
    llm_timeout: int

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url)


def load_settings() -> Settings:
    return Settings(
        pluggy_client_id=os.environ.get("PLUGGY_CLIENT_ID", ""),
        pluggy_client_secret=os.environ.get("PLUGGY_CLIENT_SECRET", ""),
        firefly_base_url=os.environ.get("FIREFLY_BASE_URL", "http://umbrel.local:30009").rstrip("/"),
        firefly_token=os.environ.get("FIREFLY_TOKEN", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        monthly_savings_target=_float("MONTHLY_SAVINGS_TARGET", 0.0),
        safety_buffer=_float("SAFETY_BUFFER", 0.0),
        report_hour_utc=_int("REPORT_HOUR_UTC", 8),  # 05:00 em Brasília
        review_hour_utc=_int("REVIEW_HOUR_UTC", 6),  # 03:00 em Brasília (janela da GPU)
        insights_weekday_utc=_int("INSIGHTS_WEEKDAY_UTC", 6),  # 0=segunda ... 6=domingo
        insights_hour_utc=_int("INSIGHTS_HOUR_UTC", 12),  # 09:00 em Brasília
        sync_every_hours=_int("SYNC_EVERY_HOURS", 3),
        sync_since_days=_int("SYNC_SINCE_DAYS", 7),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").rstrip("/"),
        llm_model=os.environ.get("LLM_MODEL", "qwen2.5:7b-instruct"),
        llm_timeout=_int("LLM_TIMEOUT", 120),
    )


def _read_json(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_account_mapping() -> dict[str, dict]:
    """{pluggy_account_id: entrada do mapeamento}."""
    data = _read_json("account_mapping.json")
    return {m["pluggy_account_id"]: m for m in data["mappings"]}


@lru_cache(maxsize=1)
def load_category_rules_file() -> dict:
    return _read_json("category_rules.json")


@lru_cache(maxsize=1)
def load_category_rules() -> list[dict]:
    rules = load_category_rules_file()["rules"]
    return [{**r, "_match": r["match"].lower()} for r in rules]


@lru_cache(maxsize=1)
def load_price_signals_config() -> dict:
    try:
        return _read_json("price_signals.json")
    except FileNotFoundError:
        return {}


@lru_cache(maxsize=1)
def load_llm_windows() -> dict:
    try:
        return _read_json("llm_janelas.json")
    except FileNotFoundError:
        return {"janelas": [], "keep_alive_dentro": "5m", "keep_alive_fora": "1m"}
