"""Fatos financeiros calculados em Python, prontos para o gestor (LLM) explicar e para o bot responder.

Tudo aqui é determinístico e testável: o LLM recebe o resultado, nunca os dados crus
para somar. Movimentações internas (categoria "Transferência interna") ficam fora.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from app import clock
from app.finmath import effective_monthly_cost
from app.ml import merchant_key
from app.normalize import INSTALLMENT_TAG, INTERNAL_CATEGORY

POKEMON = "Pokémon TCG"
CARD_SALES = "Venda de cartas"


def _d(s: dict) -> date:
    return date.fromisoformat(str(s["date"])[:10])


def _amt(s: dict) -> float:
    return float(s.get("amount") or 0)


def is_internal(s: dict) -> bool:
    return s.get("category_name") == INTERNAL_CATEGORY or "interno" in (s.get("tags") or [])


def spending(splits: list[dict]) -> list[dict]:
    """Só gastos de verdade (saídas, sem movimentação interna)."""
    return [s for s in splits if s.get("type", "withdrawal") == "withdrawal" and not is_internal(s)]


def by_category(splits: list[dict], start: date, end: date) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for s in spending(splits):
        if start <= _d(s) <= end:
            out[s.get("category_name") or "Sem categoria"] += _amt(s)
    return {k: round(v, 2) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def month_summary(splits: list[dict], today: date) -> dict:
    start = today.replace(day=1)
    cats = by_category(splits, start, today)
    total = round(sum(cats.values()), 2)
    return {"inicio": start.isoformat(), "total": total, "categorias": cats,
            "sem_categoria_pct": round(cats.get("Sem categoria", 0) / total * 100, 1) if total else 0.0}


def installments_ahead(splits: list[dict], today: date, months: int = 6) -> list[dict]:
    """Parcelas já lançadas com data futura (tag "parcela"), somadas por mês."""
    by_month: dict[str, float] = defaultdict(float)
    for s in spending(splits):
        if _d(s) > today and INSTALLMENT_TAG in (s.get("tags") or []):
            by_month[_d(s).strftime("%Y-%m")] += _amt(s)
    keys = sorted(by_month)[:months]
    return [{"mes": k, "total": round(by_month[k], 2)} for k in keys]


_PIX_CARD = re.compile(r"\bpix\b", re.I)
_INST_END = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*$")


def _card_pix_candidates(splits: list[dict], since: date) -> list[dict]:
    """Cobranças de Pix no cartão: avulsas, ou parcelas da mesma compra somadas (mesmo texto, valor e nº de parcelas)."""
    singles, groups = [], defaultdict(list)
    for s in spending(splits):
        desc = s.get("description") or ""
        if not _PIX_CARD.search(desc) or "pix enviado com cart" in desc.lower():
            continue
        m = _INST_END.search(desc)
        if m:
            stem = _INST_END.sub("", desc).strip().lower()
            groups[(stem, int(m.group(2)), round(_amt(s), 2), s.get("source_name"))].append(s)
        elif _d(s) >= since:
            singles.append({"parcelas": [_amt(s)], "total": round(_amt(s), 2), "dia": _d(s)})
    for items in groups.values():
        first = min(_d(x) for x in items)
        if first >= since:
            singles.append({"parcelas": [_amt(x) for x in items], "total": round(sum(_amt(x) for x in items), 2), "dia": first})
    return singles


def pix_credit_costs(splits: list[dict], days: int = 60, today: date | None = None) -> list[dict]:
    """Custo do Pix no crédito: o que saiu para o recebedor (lado da conta, marcado como interno)
    contra o que o cartão cobra (avulso ou soma das parcelas). Casa por valor até 15% maior e até 3 dias."""
    today = today or clock.today()
    since = today - timedelta(days=days)
    sent = [s for s in splits if is_internal(s) and "pix enviado com cart" in (s.get("description") or "").lower()
            and _d(s) >= since and s.get("type", "withdrawal") == "withdrawal"]
    cands = _card_pix_candidates(splits, since - timedelta(days=3))
    out, used = [], set()
    for s in sorted(sent, key=_amt):
        a = _amt(s)
        best = None
        for i, g in enumerate(cands):
            if i in used or not (a <= g["total"] <= a * 1.15) or abs((g["dia"] - _d(s)).days) > 3:
                continue
            if best is None or g["total"] - a < cands[best]["total"] - a:
                best = i
        if best is None:
            continue
        used.add(best)
        g = cands[best]
        out.append({"data": str(_d(s)), "enviado": round(a, 2), "cobrado": g["total"], "parcelas": len(g["parcelas"]),
                    "taxa_paga": round(g["total"] - a, 2), "custo_mes_pct": effective_monthly_cost(a, g["parcelas"])})
    return sorted(out, key=lambda r: r["data"])


def pokemon_month(splits: list[dict], today: date, budget: float | None) -> dict:
    """Pokémon é consumo: o gasto conta no orçamento; vendas de cartas (Pix recebido) só mostram o líquido."""
    start = today.replace(day=1)
    spent = sum(_amt(s) for s in spending(splits) if s.get("category_name") == POKEMON and start <= _d(s) <= today)
    future = sum(_amt(s) for s in spending(splits) if s.get("category_name") == POKEMON and _d(s) > today)
    sales = sum(_amt(s) for s in splits if s.get("type") == "deposit" and s.get("category_name") == CARD_SALES
                and not is_internal(s) and start <= _d(s) <= today)
    return {"gasto_mes": round(spent, 2), "vendas_mes": round(sales, 2), "liquido_mes": round(spent - sales, 2),
            "parcelas_futuras": round(future, 2), "orcamento": budget,
            "uso_orcamento_pct": round(spent / budget * 100, 1) if budget else None}


def top_merchants(splits: list[dict], start: date, end: date, n: int = 5) -> list[dict]:
    agg: dict[str, list[float]] = defaultdict(list)
    for s in spending(splits):
        if start <= _d(s) <= end:
            agg[merchant_key(s.get("description", "")) or "?"].append(_amt(s))
    rows = [{"estabelecimento": k, "total": round(sum(v), 2), "vezes": len(v)} for k, v in agg.items()]
    return sorted(rows, key=lambda r: -r["total"])[:n]


def goals_progress(piggies: list[dict], monthly_saving: float, rate_am_pct: float) -> list[dict]:
    from app.finmath import months_to_goal
    share = monthly_saving / len(piggies) if piggies and monthly_saving else 0.0
    out = []
    for p in piggies:
        missing = max(0.0, p["target"] - p["current"])
        out.append({"meta": p["name"], "alvo": p["target"], "atual": p["current"], "falta": round(missing, 2),
                    "meses_no_ritmo": months_to_goal(p["target"], share, rate_am_pct, p["current"]) if share else None})
    return out
