"""Diagnóstico rápido (Console do container): faturas calculadas por cartão e Banco Central.

    /app/.venv/bin/python scripts/diagnose.py

Mostra, por cartão: faturas fechadas que a Pluggy devolve (campos e datas), o ciclo usado,
quantas compras caíram nele e as maiores. Não grava nada e não imprime credencial.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import invoices  # noqa: E402
from app.clients.pluggy import PluggyClient  # noqa: E402
from app.config import load_account_mapping, load_settings  # noqa: E402
from app.normalize import shift_installments  # noqa: E402


def cards(pluggy: PluggyClient, today: date) -> None:
    for pid, m in load_account_mapping().items():
        if not m.get("is_credit_card"):
            continue
        name = m["firefly_account_name"]
        try:
            acc = pluggy.get_account(pid)
            bills = pluggy.list_bills(pid)
            txns = pluggy.list_transactions(pid, invoices.LOOKBACK_DAYS)
        except Exception as exc:  # noqa: BLE001
            print(f"\n## {name}: erro {type(exc).__name__}: {exc}")
            continue
        credit = acc.get("creditData") or {}
        print(f"\n## {name}  (balance {acc.get('balance')}, limite {credit.get('creditLimit')}, "
              f"disponível {credit.get('availableCreditLimit')}, vencimento informado {credit.get('balanceDueDate')})")
        print(f"faturas fechadas: {len(bills)}")
        for b in sorted(bills, key=lambda b: str(b.get('dueDate')))[-2:]:
            print("  campos:", sorted(b.keys()))
            print("  ", {k: b.get(k) for k in ("dueDate", "billClosingDate", "closeDate", "totalAmount")})
        due = invoices._date(credit.get("balanceDueDate"))
        cycle = invoices.cycle_from_bills(bills, today, due.day if due else None)
        print(f"compras devolvidas pela Pluggy ({invoices.LOOKBACK_DAYS} dias): {len(txns)}"
              + (f", de {min(t['date'][:10] for t in txns)} a {max(t['date'][:10] for t in txns)}" if txns else ""))
        if not cycle:
            print("sem ciclo (sem faturas e sem vencimento)")
            continue
        print(f"ciclo: fechou {cycle['last_close']}, fecha {cycle['next_close']}, vence {cycle['next_due']} ({cycle['fonte']})")
        shifted = shift_installments([("c", {"is_credit_card": True}, t) for t in txns])
        inside = [t for t in txns
                  if cycle["last_close"] < invoices._date((shifted.get(t["id"]) or {}).get("date") or t["date"]) <= cycle["next_close"]]
        print(f"no ciclo: {len(inside)} lançamentos, fatura calculada R$ "
              f"{invoices.open_amount(txns, cycle['last_close'], cycle['next_close']):.2f}")
        for t in sorted(inside, key=lambda t: -abs(float(t['amount'])))[:6]:
            print(f"   {t['date'][:10]} {float(t['amount']):>9.2f}  {(t.get('description') or '')[:40]}")


def bcb() -> None:
    import requests

    from app.market import BASE, HEADERS
    for code in (432, 433, 13522, 4389):
        url = BASE.format(code=code) + "/ultimos/1?formato=json"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            print(f"BCB {code}: HTTP {r.status_code} {r.text[:80]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"BCB {code}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    s = load_settings()
    cards(PluggyClient(s.pluggy_client_id, s.pluggy_client_secret), date.today())
    print()
    bcb()
