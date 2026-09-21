"""Calcula quanto dá pra gastar e quanto guardar hoje, e envia pelo canal escolhido.

MVP: enquanto orçamentos por categoria não existirem no Firefly, o cálculo é
por saldo total, não por categoria. Rode `python sync.py` antes deste script
(via cron, por exemplo) pra garantir que o saldo já reflete os gastos do dia.

Canal de envio (NOTIFY_CHANNEL no .env):
- "telegram" (padrão): manda direto pra API do Telegram.
- "webhook": manda um POST em JSON pra WEBHOOK_URL — um gatilho do n8n ou do
  Node-RED, que decide pra onde a mensagem vai de verdade (WhatsApp, e-mail,
  o que for). Esse script não precisa saber, nem mudar, quando o canal mudar.
"""
import calendar
from datetime import date

from config import load_account_mapping, load_settings
from firefly_client import FireflyClient
from telegram_notify import send_telegram_message
from webhook_notify import send_webhook


def get_checking_account_ids(mapping: dict) -> list[int]:
    """Ids das contas correntes reais (não cartão) no Firefly, derivados do
    account_mapping.json. Antes isso era um id fixo (CONTA_CORRENTE_ID = 15,
    a conta genérica antiga que ficou obsoleta com a integração da Pluggy) —
    trocado por isso pra não depender de um id parado no tempo: se um banco
    novo entrar no account_mapping.json, o relatório já soma ele também.
    """
    ids = {m["firefly_account_id"] for m in mapping.values() if not m.get("is_credit_card")}
    return sorted(ids)


def fmt_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def compute_report(
    firefly: FireflyClient, mapping: dict, monthly_savings_target: float, safety_buffer: float
) -> dict:
    """Retorna os números crus (não o texto formatado), pra quem for enviar
    escolher o formato: texto simples, cartão do WhatsApp, JSON pro n8n, etc.
    """
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_remaining = days_in_month - today.day + 1  # inclui hoje

    checking_ids = get_checking_account_ids(mapping)
    balance = sum(
        float(firefly.get_account(acc_id)["attributes"]["current_balance"]) for acc_id in checking_ids
    )

    due = firefly.list_recurring_due_this_month()
    total_due = sum(item["amount"] for item in due)

    free_to_spend_today = max(0.0, (balance - total_due - safety_buffer) / days_remaining)
    save_today = monthly_savings_target / days_in_month

    return {
        "date": today.isoformat(),
        "balance": round(balance, 2),
        "fixed_bills_due": round(total_due, 2),
        "fixed_bills_count": len(due),
        "days_remaining_in_month": days_remaining,
        "free_to_spend_today": round(free_to_spend_today, 2),
        "save_today": round(save_today, 2),
        "note": (
            "Saldo somado das contas correntes conectadas via Pluggy "
            f"({len(checking_ids)} conta(s)). Cálculo por saldo total, ainda não "
            "considera orçamento por categoria (pendente configurar Orçamentos no "
            "Firefly). Não inclui a parte do Bradesco Black (fatura conjunta, "
            "ajuste manual mensal)."
        ),
    }


def format_report_text(report: dict) -> str:
    day = date.fromisoformat(report["date"]).strftime("%d/%m/%Y")
    return "\n".join([
        f"*Relatório financeiro — {day}*",
        "",
        f"Saldo atual: {fmt_brl(report['balance'])}",
        f"Contas fixas que ainda vão vencer este mês: {fmt_brl(report['fixed_bills_due'])} "
        f"({report['fixed_bills_count']} itens)",
        f"Dias restantes no mês (incluindo hoje): {report['days_remaining_in_month']}",
        "",
        f"💸 Pode gastar hoje (livre, sem tocar nas contas fixas): *{fmt_brl(report['free_to_spend_today'])}*",
        f"🏦 Guardar hoje (meta de poupança mensal ÷ dias do mês): *{fmt_brl(report['save_today'])}*",
        "",
        f"_{report['note']}_",
    ])


if __name__ == "__main__":
    settings = load_settings()
    mapping = load_account_mapping()
    firefly = FireflyClient(settings.firefly_base_url, settings.firefly_token)
    report = compute_report(firefly, mapping, settings.monthly_savings_target, settings.safety_buffer)
    text = format_report_text(report)
    print(text)

    if settings.notify_channel == "webhook":
        send_webhook(settings.webhook_url, {**report, "text": text})
    else:
        send_telegram_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
