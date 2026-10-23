"""Conferência rápida: o que o bot vai dizer, conta por conta, para comparar com o app do banco.

Mostra, por conexão, quando a Pluggy atualizou pela última vez (e a próxima), e por conta:
saldo que a Pluggy devolve, data do último lançamento gravado no Firefly e, no cartão,
a fatura calculada. Se o saldo daqui bate com o do banco no horário da atualização, o erro
está no cálculo; se não bate, o dado da Pluggy está atrasado.

Uso (Console do container): /app/.venv/bin/python scripts/conferir.py
Só lê; não grava nada nem manda mensagem.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clock, freshness, invoices  # noqa: E402
from app.clients.firefly import FireflyClient  # noqa: E402
from app.clients.pluggy import PluggyClient  # noqa: E402
from app.config import load_account_mapping, load_settings  # noqa: E402


def main() -> None:
    s = load_settings()
    pluggy = PluggyClient(s.pluggy_client_id, s.pluggy_client_secret)
    ff = FireflyClient(s.firefly_base_url, s.firefly_token)
    mapping = load_account_mapping()
    today = clock.today()
    print(f"Agora em Brasília: {clock.now():%d/%m %H:%M}\n")

    conns = freshness.connections(pluggy, mapping)
    for c in conns:
        up = f"{c['atualizado']:%d/%m %H:%M}" if c["atualizado"] else "?"
        nxt = f"{c['proxima']:%d/%m %H:%M}" if c["proxima"] else "?"
        print(f"[{c['banco']}] status {c['status']}, atualizou {up}, próxima {nxt}: {', '.join(c['contas'])}")
    print(freshness.line(conns), "\n")

    last_by_acc: dict[int, str] = {}
    for sp in ff.iter_splits(today - timedelta(days=10), today, "all"):
        acc = sp.get("source_id") if sp.get("type") == "withdrawal" else sp.get("destination_id")
        d = str(sp.get("date"))[:10]
        if acc and d > last_by_acc.get(int(acc), ""):
            last_by_acc[int(acc)] = d

    estimates = invoices.estimates_cached(pluggy, mapping, today, freshness.version(conns))
    total = 0.0
    for pid, m in sorted(mapping.items(), key=lambda kv: kv[1]["firefly_account_name"]):
        name = m["firefly_account_name"]
        try:
            acc = pluggy.get_account(pid)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: erro {type(exc).__name__}")
            continue
        last = last_by_acc.get(int(m["firefly_account_id"]), "nenhum nos últimos 10 dias")
        if m.get("is_credit_card"):
            est = estimates.get(name) or {}
            print(f"💳 {name}: fatura calculada R$ {est.get('amount', 0):.2f} (sua parte R$ {est.get('my_amount', 0):.2f}),"
                  f" vence {est.get('due_date')}; balance da Pluggy {acc.get('balance')}; último no Firefly {last}")
        else:
            total += float(acc.get("balance") or 0)
            print(f"🏦 {name}: saldo Pluggy R$ {float(acc.get('balance') or 0):.2f}; último no Firefly {last}")
    print(f"\nSaldo somado das contas (o 'Saldo real' do bot): R$ {total:.2f}")


if __name__ == "__main__":
    main()
