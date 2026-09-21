"""Carrega configuração de .env, account_mapping.json e category_rules.json."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass
class Settings:
    pluggy_client_id: str
    pluggy_client_secret: str
    firefly_base_url: str
    firefly_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    notify_channel: str
    webhook_url: str
    monthly_savings_target: float
    safety_buffer: float


def load_settings() -> Settings:
    return Settings(
        pluggy_client_id=os.environ.get("PLUGGY_CLIENT_ID", ""),
        pluggy_client_secret=os.environ.get("PLUGGY_CLIENT_SECRET", ""),
        firefly_base_url=os.environ.get("FIREFLY_BASE_URL", "http://umbrel.local:30009").rstrip("/"),
        firefly_token=os.environ.get("FIREFLY_TOKEN", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        # "telegram" (padrão, direto pra API do Telegram) ou "webhook" (dispara um
        # gatilho do n8n/Node-RED, que decide pra onde a mensagem vai de verdade)
        notify_channel=os.environ.get("NOTIFY_CHANNEL", "telegram"),
        webhook_url=os.environ.get("WEBHOOK_URL", ""),
        monthly_savings_target=float(os.environ.get("MONTHLY_SAVINGS_TARGET", "0") or 0),
        safety_buffer=float(os.environ.get("SAFETY_BUFFER", "0") or 0),
    )


def load_account_mapping() -> dict:
    path = BASE_DIR / "account_mapping.json"
    if not path.exists():
        raise FileNotFoundError(
            "account_mapping.json não encontrado. Copie account_mapping.example.json e preencha os ids."
        )
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {m["pluggy_account_id"]: m for m in data["mappings"]}


def load_category_rules() -> list:
    path = BASE_DIR / "category_rules.json"
    if not path.exists():
        path = BASE_DIR / "category_rules.example.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["rules"]


def guess_category(description: str) -> dict:
    desc = (description or "").lower()
    for rule in load_category_rules():
        if rule["match"].lower() in desc:
            return rule
    return {}
