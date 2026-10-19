"""Bot do Telegram: envio (com botões) e leitura de mensagens (long polling).

Long polling: o bot pergunta "tem mensagem nova?" e o Telegram segura a conexão até 50 s
esperando uma. É simples, não precisa abrir porta nem ter domínio (webhook precisaria).
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)
MAX_LEN = 4000  # limite do Telegram é 4096


def _api(token: str, method: str, payload: dict, timeout: int = 20) -> dict:
    resp = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=timeout)
    if resp.status_code == 400 and payload.get("parse_mode"):
        # Markdown quebrado (ex.: "_" numa descrição de banco): manda de novo sem formatação
        resp = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             json={k: v for k, v in payload.items() if k != "parse_mode"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("result") or {}


def keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """[[("texto", "callback_data"), ...], ...] -> inline_keyboard do Telegram."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in row] for row in rows]}


def send_message(bot_token: str, chat_id: str, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> dict:
    if not bot_token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes; mensagem só no log")
        print(text)
        return {}
    chunks = [text[i : i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
    result = {}
    for n, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
        if buttons and n == len(chunks) - 1:
            payload["reply_markup"] = keyboard(buttons)
        result = _api(bot_token, "sendMessage", payload)
    return result


def edit_message(bot_token: str, chat_id: str, message_id: int, text: str,
                 buttons: list[list[tuple[str, str]]] | None = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text[:MAX_LEN], "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = keyboard(buttons)
    try:
        _api(bot_token, "editMessageText", payload)
    except requests.RequestException as exc:
        log.warning("não consegui editar a mensagem: %s", exc)


def answer_callback(bot_token: str, callback_id: str, text: str = "") -> None:
    try:
        _api(bot_token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})
    except requests.RequestException:
        pass


def get_updates(bot_token: str, offset: int | None, timeout: int = 50) -> list[dict]:
    payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    resp = requests.post(f"https://api.telegram.org/bot{bot_token}/getUpdates", json=payload, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result") or []


def set_commands(bot_token: str, commands: list[tuple[str, str]]) -> None:
    try:
        _api(bot_token, "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})
    except requests.RequestException as exc:
        log.warning("setMyCommands falhou: %s", exc)
