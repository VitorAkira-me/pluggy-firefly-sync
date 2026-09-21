import requests


def send_webhook(webhook_url: str, payload: dict) -> None:
    """Manda o relatório como JSON pra um webhook (gatilho do n8n ou do Node-RED).

    O workflow do outro lado decide o que fazer com isso: mandar por WhatsApp,
    Telegram, e-mail, o que for. Esse script não precisa saber pra onde vai.
    """
    if not webhook_url:
        print("[webhook_notify] WEBHOOK_URL não configurada no .env — nada enviado.")
        return
    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()
