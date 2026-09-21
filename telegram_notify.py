import requests


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        print("[telegram_notify] Faltam TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no .env — mensagem não enviada:")
        print(text)
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    resp.raise_for_status()
