"""Fatura aberta de cada cartão, calculada sozinha (sem precisar do /fatura).

A Pluggy não informa a fatura que ainda está aberta, mas informa as faturas já
FECHADAS (/bills, com data de fechamento e vencimento) e todas as compras do cartão.
Então:

    fatura aberta = compras com data efetiva depois do último fechamento
                    e até o próximo fechamento (último + 1 mês)
                    - estornos (devoluções)
    vencimento     = último vencimento + 1 mês (avança até ficar no futuro)

"Data efetiva" resolve as parcelas: alguns bancos mandam as 10 parcelas de uma vez na
data da compra; cada parcela k conta no mês da compra + (k - 1) (app/normalize.py).
Pagamentos de fatura não entram (são movimentação interna).

Prioridade usada pelo relatório:
1. valor informado no bot (/fatura) DEPOIS do último fechamento (corrige este ciclo);
2. esta estimativa;
3. `manual_invoice` antigo do mapeamento ou o `balance` da Pluggy (último recurso).
Cartão dividido (Bradesco Black, `share` 0.5): o valor que entra no fluxo é a sua parte.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

from app import state
from app.normalize import internal_patterns, is_card_payment, shift_installments

log = logging.getLogger(__name__)
LOOKBACK_DAYS = 400   # pega compras parceladas antigas cujas parcelas ainda caem neste ciclo
DEFAULT_CLOSE_BEFORE_DUE = 7


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def share_of(target: dict) -> float:
    if "share" in target:
        return float(target["share"])
    return 0.5 if target.get("shared") or target.get("firefly_account_name") == "Bradesco Black" else 1.0


def cycle_from_bills(bills: list[dict], today: date, fallback_due_day: int | None) -> dict | None:
    """Último fechamento conhecido, próximo fechamento e próximo vencimento."""
    closed = []
    for b in bills:
        due = _date(b.get("dueDate"))
        close = _date(b.get("billClosingDate") or b.get("closeDate") or b.get("closingDate"))
        if due:
            closed.append((close or due - timedelta(days=DEFAULT_CLOSE_BEFORE_DUE), due))
    if closed:
        last_close, last_due = max(closed, key=lambda c: c[1])
        source = "faturas fechadas"
    elif fallback_due_day:
        due_this = date(today.year, today.month, min(fallback_due_day, calendar.monthrange(today.year, today.month)[1]))
        last_due = due_this if due_this <= today else _add_months(due_this, -1)
        last_close = last_due - timedelta(days=DEFAULT_CLOSE_BEFORE_DUE)
        source = "dia de vencimento"
    else:
        return None
    next_close, next_due = _add_months(last_close, 1), _add_months(last_due, 1)
    while next_close < today:   # faturas fechadas atrasadas na Pluggy: avança o ciclo
        last_close, last_due = next_close, next_due
        next_close, next_due = _add_months(last_close, 1), _add_months(last_due, 1)
    return {"last_close": last_close, "next_close": next_close, "next_due": next_due, "fonte": source}


def open_amount(transactions: list[dict], last_close: date, next_close: date,
                bill_totals: set[float] | None = None) -> float:
    """Soma das compras (positivas na Pluggy) menos estornos no ciclo, pela data efetiva.

    Crédito que é pagamento (texto de pagamento/boleto ou valor igual ao total de uma fatura
    fechada) não abate: ele paga a fatura anterior, não a que está aberta."""
    pats = internal_patterns()
    target = {"is_credit_card": True}
    batch = [("card", target, t) for t in transactions]
    shifted = shift_installments(batch)
    totals = {round(float(v), 2) for v in (bill_totals or set())}
    total = 0.0
    for t in transactions:
        desc = (t.get("description") or "").lower()
        amount = float(t["amount"])
        if amount < 0 and (any(p in desc for p in pats) or is_card_payment(t, target)
                           or round(-amount, 2) in totals):
            continue  # pagamento da fatura
        eff = _date((shifted.get(t["id"]) or {}).get("date") or t["date"])
        if eff and last_close < eff <= next_close:
            total += amount
    return round(max(total, 0.0), 2)


def estimate(pluggy, pid: str, target: dict, today: date) -> dict | None:
    """{"amount", "my_amount", "due_date", "last_close", "fonte"} ou None se não der pra calcular."""
    try:
        acc = pluggy.get_account(pid)
        raw_due = (acc.get("creditData") or {}).get("balanceDueDate")
        fallback_day = _date(raw_due).day if _date(raw_due) else None
        bills = pluggy.list_bills(pid)
        cycle = cycle_from_bills(bills, today, fallback_day)
        if not cycle:
            return None
        txns = pluggy.list_transactions(pid, LOOKBACK_DAYS)
        totals = {float(b["totalAmount"]) for b in bills if b.get("totalAmount")}
        amount = open_amount(txns, cycle["last_close"], cycle["next_close"], totals)
    except Exception as exc:  # noqa: BLE001 (sem estimativa, o relatório cai no próximo recurso)
        log.warning("estimativa da fatura de %s falhou: %s", target.get("firefly_account_name"), exc)
        return None
    share = share_of(target)
    return {"amount": amount, "my_amount": round(amount * share, 2), "share": share,
            "due_date": cycle["next_due"].isoformat(), "last_close": cycle["last_close"].isoformat(),
            "next_close": cycle["next_close"].isoformat(), "fonte": cycle["fonte"]}


def valid_override(name: str, last_close: str | None) -> dict | None:
    """Valor do /fatura só vale se foi informado depois do último fechamento (é deste ciclo)."""
    entry = state.manual_invoices().get(name)
    if not entry:
        return None
    if last_close and entry.get("updated_at", "") <= last_close:
        return None
    return entry


def estimates_cached(pluggy, mapping: dict, today: date, data_version: str = "") -> dict[str, dict | None]:
    """Uma estimativa por cartão por dia E por versão do dado da Pluggy.

    Antes era só por dia: calculada às 05:00, antes de o banco atualizar, ficava velha o dia inteiro.
    `data_version` (app/freshness.version) muda quando qualquer conexão atualiza."""
    cache = state.get("invoice_estimates") or {}
    if cache.get("dia") == today.isoformat() and cache.get("versao", "") == data_version and data_version:
        return cache["cartoes"]
    out = {m["firefly_account_name"]: estimate(pluggy, pid, m, today)
           for pid, m in mapping.items() if m.get("is_credit_card")}
    state.put("invoice_estimates", {"dia": today.isoformat(), "versao": data_version, "cartoes": out})
    return out
