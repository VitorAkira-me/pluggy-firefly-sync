"""Resumo financeiro diário: quanto dá pra gastar hoje, quanto guardar, e por quê.

Fontes: saldo das contas correntes e faturas de cartão vêm da Pluggy (valor real
do banco); contas fixas e salário vêm das recorrências do Firefly; gasto do mês
vem das transações do Firefly. Uso: `python -m app.report [--no-send]`.
"""
from __future__ import annotations

import argparse
import calendar
import logging
from datetime import date, timedelta

from app import state
from app.clients.firefly import FireflyClient, next_occurrence
from app.clients.pluggy import PluggyClient
from app.clients.telegram import send_message
from app.config import load_account_mapping, load_settings
from app.normalize import INTERNAL_CATEGORY

log = logging.getLogger("report")
HORIZON_DAYS = 30


def fmt_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def compute_safe_daily_spend(balance: float, events: list[dict], today: date, safety_buffer: float, horizon_days: int) -> float:
    """Maior gasto diário constante que não deixa o saldo projetado abaixo do colchão
    em nenhuma data de SAÍDA prevista na janela (contas e faturas), contando o
    salário que entra antes de cada uma. É o "ponto mais apertado" dos próximos dias.

    Para cada checkpoint c: (saldo + entradas_até_c - saídas_até_c - colchão) / dias_até_c.
    O resultado é o menor desses valores (nunca negativo)."""
    outflow_dates = sorted({date.fromisoformat(e["date"]) for e in events if e["direction"] == "expense"})
    end = today + timedelta(days=horizon_days - 1)
    checkpoints = outflow_dates if end in outflow_dates else outflow_dates + [end]
    best = None
    for checkpoint in checkpoints:
        n = (checkpoint - today).days + 1
        if n <= 0:
            continue
        net = sum(
            e["amount"] if e["direction"] == "income" else -e["amount"]
            for e in events
            if date.fromisoformat(e["date"]) <= checkpoint
        )
        candidate = (balance + net - safety_buffer) / n
        best = candidate if best is None else min(best, candidate)
    if best is None:
        best = (balance - safety_buffer) / horizon_days
    return max(0.0, best)


def card_item(pluggy: PluggyClient, pid: str, target: dict, today: date, overrides: dict | None = None) -> dict:
    """Valor e vencimento da próxima fatura de um cartão.

    Prioridade: valor informado pelo bot (/fatura, guardado no estado) > `manual_invoice` do
    account_mapping.json > `balance` da Pluggy (que em alguns bancos inclui parcelas futuras)."""
    manual = (overrides or {}).get(target["firefly_account_name"]) or target.get("manual_invoice")
    if manual and not manual.get("day"):
        manual = {**manual, "day": (target.get("manual_invoice") or {}).get("day") or 1}
    if manual:
        amount, due, updated = float(manual["amount"]), next_occurrence(int(manual["day"]), today), manual.get("updated_at")
    else:
        acc = pluggy.get_account(pid)
        amount = float(acc.get("balance") or 0)
        raw_due = (acc.get("creditData") or {}).get("balanceDueDate")
        due = next_occurrence(date.fromisoformat(raw_due[:10]).day, today) if raw_due else None
        updated = None
    return {
        "name": target["firefly_account_name"],
        "amount": round(amount, 2),
        "due_date": due.isoformat() if due else None,
        "shared": bool(target.get("shared")) or target["firefly_account_name"] == "Bradesco Black",
        "manual": bool(manual),
        "my_share": bool(manual and manual.get("minha_parte")),
        "updated_at": updated,
    }


def compute_report(pluggy: PluggyClient, firefly: FireflyClient, mapping: dict, savings_target: float,
                   safety_buffer: float, today: date | None = None) -> dict:
    today = today or date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    checking = sorted(pid for pid, m in mapping.items() if not m.get("is_credit_card"))
    cards = sorted(((pid, m) for pid, m in mapping.items() if m.get("is_credit_card")),
                   key=lambda kv: kv[1]["firefly_account_name"])
    card_ids = {m["firefly_account_id"] for _, m in cards}

    balance = sum(float(pluggy.get_account(pid)["balance"]) for pid in checking)

    events = firefly.list_recurring_events(HORIZON_DAYS, today)
    income_events = [e for e in events if e["direction"] == "income"]
    # recorrência paga NO cartão (ex.: assinatura) já está dentro da fatura: não contar duas vezes
    bill_events = [e for e in events if e["direction"] == "expense" and e["source_id"] not in card_ids]
    fixed_bills = sorted(
        ({"title": e["title"], "day": date.fromisoformat(e["date"]).day, "amount": round(e["amount"], 2)}
         for e in bill_events if date.fromisoformat(e["date"]).month == today.month),
        key=lambda i: i["day"],
    )

    overrides = state.manual_invoices()
    card_items = sorted((card_item(pluggy, pid, m, today, overrides) for pid, m in cards),
                        key=lambda i: i["due_date"] or "9999-12-31")
    card_events = [{"date": c["due_date"], "amount": c["amount"], "direction": "expense"}
                   for c in card_items if c["due_date"] and c["amount"] > 0]

    month_start = today.replace(day=1)
    expenses = [x for x in firefly.iter_splits(month_start, today, "withdrawal")
                if x.get("category_name") != INTERNAL_CATEGORY and "interno" not in (x.get("tags") or [])]
    total_spent = sum(float(s.get("amount") or 0) for s in expenses)
    by_cat: dict[str, float] = {}
    for s in expenses:
        cat = s.get("category_name") or "Sem categoria"
        by_cat[cat] = by_cat.get(cat, 0.0) + float(s.get("amount") or 0)
    uncategorized = by_cat.get("Sem categoria", 0.0)

    free_today = compute_safe_daily_spend(balance, bill_events + income_events + card_events, today, safety_buffer, HORIZON_DAYS)
    return {
        "date": today.isoformat(),
        "balance": round(balance, 2),
        "checking_accounts_count": len(checking),
        "spent_this_month": round(total_spent, 2),
        "spent_this_month_count": len(expenses),
        "uncategorized_pct": round(uncategorized / total_spent * 100, 1) if total_spent else 0.0,
        "top_categories": [
            {"name": n, "amount": round(a, 2), "pct": round(a / total_spent * 100, 1) if total_spent else 0.0}
            for n, a in sorted(by_cat.items(), key=lambda kv: -kv[1]) if n != "Sem categoria"
        ][:3],
        "fixed_bills_items": fixed_bills,
        "fixed_bills_due": round(sum(i["amount"] for i in fixed_bills), 2),
        "card_items": card_items,
        "cards_due_total": round(sum(c["amount"] for c in card_items), 2),
        "free_to_spend_today": round(free_today, 2),
        "save_today": round(savings_target / days_in_month, 2),
    }


def format_report_text(r: dict) -> str:
    day = date.fromisoformat(r["date"]).strftime("%d/%m/%Y")
    lines = [
        f"📊 *Resumo financeiro, {day}*",
        "",
        f"💸 Pode gastar hoje: *{fmt_brl(r['free_to_spend_today'])}*",
        f"🏦 Guardar hoje: *{fmt_brl(r['save_today'])}*",
        "",
        f"💰 Saldo real: {fmt_brl(r['balance'])} ({r['checking_accounts_count']} contas)",
        f"📉 Gasto no mês: {fmt_brl(r['spent_this_month'])} ({r['spent_this_month_count']} transações)",
    ]
    if r["fixed_bills_items"]:
        lines += ["", f"📅 *Contas a vencer este mês*: {fmt_brl(r['fixed_bills_due'])}"]
        lines += [f"• {i['title']}: {fmt_brl(i['amount'])} (dia {i['day']:02d})" for i in r["fixed_bills_items"]]
    if r["card_items"]:
        lines += ["", f"💳 *Faturas*: {fmt_brl(r['cards_due_total'])}"]
        for c in r["card_items"]:
            due = date.fromisoformat(c["due_date"]).strftime("%d/%m") if c["due_date"] else "?"
            notes = []
            if c.get("my_share"):
                notes.append("sua parte")
            elif c["shared"]:
                notes.append("conjunta, sua parte é metade")
            if c["manual"]:
                upd = date.fromisoformat(c["updated_at"]) if c.get("updated_at") else None
                old = upd and (date.fromisoformat(r["date"]) - upd).days > 10
                notes.append(f"informado em {upd.strftime('%d/%m') if upd else '?'}" + (" ⚠️ atualize: /fatura" if old else ""))
            lines.append(f"• {c['name']}: {fmt_brl(c['amount'])} (vence {due})" + (f" ({'; '.join(notes)})" if notes else ""))
    if r["top_categories"]:
        lines += ["", "🏷 *Onde foi o dinheiro no mês:*"]
        lines += [f"{n}. {c['name']}: {fmt_brl(c['amount'])} ({c['pct']:.0f}%)" for n, c in enumerate(r["top_categories"], 1)]
    if r["uncategorized_pct"] >= 20:
        lines.append(f"⚠️ {r['uncategorized_pct']:.0f}% do gasto ainda sem categoria")
    lines += ["", "_Considera contas, faturas e salário dos próximos 30 dias._"]
    return "\n".join(lines)


def price_block() -> str:
    try:
        from app.price_signals import build_block
    except ImportError:
        return ""
    try:
        return build_block()
    except Exception as exc:  # noqa: BLE001 (sinal de preço nunca derruba o relatório)
        log.warning("sinais de preço falharam: %s", exc)
        return ""


def main(send: bool = True) -> str:
    s = load_settings()
    report = compute_report(
        PluggyClient(s.pluggy_client_id, s.pluggy_client_secret),
        FireflyClient(s.firefly_base_url, s.firefly_token),
        load_account_mapping(), s.monthly_savings_target, s.safety_buffer,
    )
    text = format_report_text(report)
    extra = price_block()
    if extra:
        text += "\n\n" + extra
    print(text)
    if send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-send", action="store_true")
    main(send=not p.parse_args().no_send)
