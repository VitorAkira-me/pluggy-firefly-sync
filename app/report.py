"""Resumo financeiro diário: quanto dá pra gastar hoje, quanto guardar, e por quê.

Fontes: saldo das contas correntes e faturas de cartão vêm da Pluggy (valor real
do banco); contas fixas e salário vêm das recorrências do Firefly; gasto do mês
vem das transações do Firefly. Uso: `python -m app.report [--no-send]`.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from app import clock, freshness, invoices, savings, state
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


def tightest_margin(balance: float, events: list[dict], today: date, safety_buffer: float, horizon_days: int) -> float:
    """Menor folga em dinheiro (saldo + entradas - saídas - colchão) em qualquer data de saída da janela."""
    outflow_dates = sorted({date.fromisoformat(e["date"]) for e in events if e["direction"] == "expense"})
    end = today + timedelta(days=horizon_days - 1)
    worst = None
    for checkpoint in (outflow_dates if end in outflow_dates else outflow_dates + [end]):
        net = sum(e["amount"] if e["direction"] == "income" else -e["amount"]
                  for e in events if date.fromisoformat(e["date"]) <= checkpoint)
        value = balance + net - safety_buffer
        worst = value if worst is None else min(worst, value)
    return worst if worst is not None else balance - safety_buffer


def card_item(pluggy: PluggyClient, pid: str, target: dict, today: date, est: dict | None = None) -> dict:
    """Próxima fatura de um cartão, do jeito mais automático possível (ver app/invoices.py).

    `amount` é o que sai do SEU bolso (no cartão dividido, a sua parte); `total` é a fatura inteira."""
    name = target["firefly_account_name"]
    share = invoices.share_of(target)
    override = invoices.valid_override(name, (est or {}).get("last_close"))
    if override:
        mine = float(override["amount"]) if override.get("minha_parte") or share == 1 else float(override["amount"]) * share
        total = (est or {}).get("amount") if share != 1 else mine
        day = override.get("day") or (target.get("manual_invoice") or {}).get("day")
        due = (est or {}).get("due_date") or (next_occurrence(int(day), today).isoformat() if day else None)
        source, updated = "bot", override.get("updated_at")
    elif est:
        mine, total, due, source, updated = est["my_amount"], est["amount"], est["due_date"], "calculada", None
    else:
        legacy = target.get("manual_invoice")
        if legacy:
            total, due = float(legacy["amount"]), next_occurrence(int(legacy["day"]), today).isoformat()
            source, updated = "mapeamento", legacy.get("updated_at")
        else:
            acc = pluggy.get_account(pid)
            total = float(acc.get("balance") or 0)
            raw_due = (acc.get("creditData") or {}).get("balanceDueDate")
            due = next_occurrence(date.fromisoformat(raw_due[:10]).day, today).isoformat() if raw_due else None
            source, updated = "pluggy", None
        mine = total * share
    return {
        "name": name,
        "amount": round(mine, 2),
        "total": round(total, 2) if total is not None else None,
        "due_date": due,
        "shared": share != 1,
        "source": source,
        "manual": source in ("bot", "mapeamento"),
        "updated_at": updated,
    }


def compute_report(pluggy: PluggyClient, firefly: FireflyClient, mapping: dict, savings_target: float,
                   safety_buffer: float, today: date | None = None, savings_rate: float = 0.5,
                   data_version: str = "") -> dict:
    """`savings_target` é o PISO da meta (MONTHLY_SAVINGS_TARGET); a meta do mês é calculada (app/savings.py)."""
    today = today or clock.today()

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

    estimates = invoices.estimates_cached(pluggy, mapping, today, data_version)
    card_items = sorted((card_item(pluggy, pid, m, today, estimates.get(m["firefly_account_name"])) for pid, m in cards),
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

    all_events = bill_events + income_events + card_events
    try:
        flows = savings.flows_cached(firefly, today)
    except Exception as exc:  # noqa: BLE001 (sem histórico a meta cai no piso; o relatório sai igual)
        log.warning("histórico para a meta de poupança falhou: %s", exc)
        flows = []
    margin = tightest_margin(balance, all_events, today, safety_buffer, HORIZON_DAYS)
    meta = savings.plan(flows, savings_target, savings_rate, margin, today)
    try:
        state.put("savings_plan", {**meta, "dia": today.isoformat()})
    except Exception:  # noqa: BLE001 (o /metas só perde a meta do dia)
        pass
    # o que ainda falta guardar no mês fica reservado: "pode gastar" e "guardar" não disputam o mesmo real
    free_today = compute_safe_daily_spend(balance, all_events, today, safety_buffer + meta["reservar_resto_do_mes"],
                                          HORIZON_DAYS)
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
        "save_today": meta["guardar_hoje"],
        "savings_plan": meta,
    }


def _meta_line(p: dict | None) -> str:
    if not p:
        return ""
    return f" (meta do mês {fmt_brl(p['meta_mes'])}: {p['motivo']})"


def format_report_text(r: dict) -> str:
    day = date.fromisoformat(r["date"]).strftime("%d/%m/%Y")
    lines = [
        f"📊 *Resumo financeiro, {day}*",
        "",
        f"💸 Pode gastar hoje: *{fmt_brl(r['free_to_spend_today'])}*",
        f"🏦 Guardar hoje: *{fmt_brl(r['save_today'])}*" + _meta_line(r.get("savings_plan")),
        "",
        f"💰 Saldo real: {fmt_brl(r['balance'])} ({r['checking_accounts_count']} contas)",
        f"📉 Gasto no mês: {fmt_brl(r['spent_this_month'])} ({r['spent_this_month_count']} transações)",
    ]
    if r["fixed_bills_items"]:
        lines += ["", f"📅 *Contas a vencer este mês*: {fmt_brl(r['fixed_bills_due'])}"]
        lines += [f"• {i['title']}: {fmt_brl(i['amount'])} (dia {i['day']:02d})" for i in r["fixed_bills_items"]]
    if r["card_items"]:
        lines += ["", f"💳 *Faturas (o que sai do seu bolso)*: {fmt_brl(r['cards_due_total'])}"]
        label = {"calculada": "calculada", "bot": "informada por você", "mapeamento": "valor antigo do mapeamento",
                 "pluggy": "saldo da Pluggy, pode incluir parcelas futuras"}
        for c in r["card_items"]:
            due = date.fromisoformat(c["due_date"]).strftime("%d/%m") if c["due_date"] else "?"
            extra = [label.get(c.get("source"), "")]
            if c.get("shared") and c.get("total"):
                extra.insert(0, f"sua parte de {fmt_brl(c['total'])}")
            lines.append(f"• {c['name']}: {fmt_brl(c['amount'])} (vence {due}; {'; '.join(x for x in extra if x)})")
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


def sync_if_behind(conns: list[dict]) -> bool:
    """Roda o sync se a Pluggy atualizou depois do último sync (o sync fixo é só a cada 3 h)."""
    newest = freshness.newest(conns)
    last = freshness._ts(state.get("last_sync_at"))
    if not newest or (last and last >= newest):
        return False
    try:
        from app.sync import sync
        sync()
        return True
    except Exception as exc:  # noqa: BLE001 (relatório sai com o que já está no Firefly)
        log.warning("sync antes do relatório falhou: %s", exc)
        return False


def main(send: bool = True, force: bool = False) -> str:
    """`force`: manda mesmo com banco atrasado (/hoje no bot e --agora no console)."""
    s = load_settings()
    pluggy = PluggyClient(s.pluggy_client_id, s.pluggy_client_secret)
    mapping = load_account_mapping()
    conns = freshness.connections(pluggy, mapping)
    late = freshness.stale(conns)
    deadline_brt = (s.report_deadline_hour_utc - 3) % 24
    if send and not force and late and clock.now().hour < deadline_brt:
        print("relatório adiado: ainda sem atualizar hoje na Pluggy: "
              + ", ".join(c["banco"] for c in late) + f" (tenta de novo na próxima hora, até {deadline_brt}:00)")
        return ""
    sync_if_behind(conns)
    report = compute_report(pluggy, FireflyClient(s.firefly_base_url, s.firefly_token), mapping,
                            s.monthly_savings_target, s.safety_buffer, savings_rate=s.savings_rate,
                            data_version=freshness.version(conns))
    text = format_report_text(report) + "\n" + freshness.line(conns)
    extra = price_block()
    if extra:
        text += "\n\n" + extra
    print(text)
    if send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
        if not force:
            state.put("report_sent_day", clock.today().isoformat())
    return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--agora", action="store_true", help="manda mesmo com banco sem atualizar hoje")
    a = p.parse_args()
    main(send=not a.no_send, force=a.agora)
