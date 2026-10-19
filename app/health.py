"""Saúde das conexões da Pluggy: avisa no Telegram antes que o dado pare de chegar.

A Pluggy atualiza cada conexão (item) sozinha uma vez por dia. Isso para em silêncio quando:
- o banco pede login de novo ou o consentimento do Open Finance vence (ele tem prazo);
- a sincronização automática foi desligada (`nextAutoSyncAt` vazio);
- o banco fica fora do ar e o item não atualiza há mais de STALE_HOURS horas.

Roda junto com o sync e manda no máximo 1 aviso por problema por dia (o estado lembra).
Uso: `python -m app.health`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app import state
from app.clients.pluggy import PluggyClient
from app.clients.telegram import send_message
from app.config import load_account_mapping, load_settings

log = logging.getLogger("health")
STALE_HOURS = 36
CONSENT_WARN_DAYS = 15
BAD_STATUS = {"LOGIN_ERROR", "OUTDATED", "WAITING_USER_INPUT", "WAITING_USER_ACTION"}


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def check_item(item: dict, now: datetime) -> list[str]:
    name = (item.get("connector") or {}).get("name") or item.get("id", "?")
    problems = []
    status = str(item.get("status") or "").upper()
    if status in BAD_STATUS:
        problems.append(f"{name}: conexão com status {status}. Abra o Meu Pluggy e reconecte o banco.")
    last = _ts(item.get("lastUpdatedAt") or item.get("updatedAt"))
    if last and now - last > timedelta(hours=STALE_HOURS):
        hours = int((now - last).total_seconds() // 3600)
        problems.append(f"{name}: sem atualização há {hours} h (o normal é 1x por dia).")
    if "nextAutoSyncAt" in item and item.get("nextAutoSyncAt") is None:
        problems.append(f"{name}: sincronização automática desligada na Pluggy.")
    consent = _ts(item.get("consentExpiresAt") or (item.get("consent") or {}).get("expiresAt"))
    if consent and consent - now < timedelta(days=CONSENT_WARN_DAYS):
        days = max(0, (consent - now).days)
        problems.append(f"{name}: consentimento do Open Finance vence em {days} dia(s). Renove no Meu Pluggy.")
    return problems


def item_ids(pluggy: PluggyClient, mapping: dict) -> set[str]:
    ids = set()
    for pid in mapping:
        try:
            item_id = pluggy.get_account(pid).get("itemId")
            if item_id:
                ids.add(item_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("conta %s sem resposta da Pluggy: %s", pid[:8], exc)
    return ids


def run(send: bool = True) -> list[str]:
    s = load_settings()
    pluggy = PluggyClient(s.pluggy_client_id, s.pluggy_client_secret)
    now = datetime.now(UTC)
    problems: list[str] = []
    for iid in sorted(item_ids(pluggy, load_account_mapping())):
        try:
            problems += check_item(pluggy.get_item(iid), now)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Conexão {iid[:8]}: não respondeu ({type(exc).__name__}).")
    today = now.date().isoformat()
    sent = state.get("health_alerts") or {}
    new = [p for p in problems if sent.get(p) != today]
    if new and send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, "🔌 *Conexões bancárias*\n" + "\n".join(f"• {p}" for p in new))
    state.put("health_alerts", {p: today for p in problems})
    print(f"Saúde: {len(problems)} problema(s), {len(new)} avisado(s) agora")
    return problems


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
