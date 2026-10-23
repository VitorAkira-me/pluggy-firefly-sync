"""Meta de poupança que se ajusta sozinha pelas contas (em vez de um valor fixo).

Ideia: só dá para guardar o que sobra. Então a meta do mês sai de dois números:

1. **Sobra típica**: mediana de (entradas - gastos) dos últimos 3 meses fechados, no
   Firefly, sem movimentação interna. Entradas incluem salário, vendas de cartas e
   reembolsos (a parte de quem divide o Black volta como entrada e compensa a metade
   dessa pessoa que está nos gastos). Mediana e não média: um mês de compra grande não derruba a meta.
2. **Folga dos próximos 30 dias**: o ponto mais apertado do saldo projetado (saldo das
   contas + salário - contas fixas - faturas) menos o colchão. É o que manda NESTE mês.

    sugerida = SAVINGS_RATE x sobra típica                (padrão 50%)
    meta     = max(sugerida, MONTHLY_SAVINGS_TARGET)      (o env vira piso, o mínimo desejado)
    meta_mes = min(meta, SAVINGS_RATE x folga_30d)        (nunca guarda o dinheiro da fatura)

Guardar hoje = meta_mes / dias do mês. A parte que ainda falta guardar no mês
fica reservada no "pode gastar hoje", para os dois números não brigarem.
Tudo em Python (regra 7): o gestor só explica o `motivo`.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from statistics import median

from app import state
from app.normalize import INTERNAL_CATEGORY

MONTHS = 3
MIN_TXNS = 5   # mês com menos lançamentos que isso não tem dado suficiente (sync parado, por ex.)


def _is_internal(s: dict) -> bool:
    return s.get("category_name") == INTERNAL_CATEGORY or "interno" in (s.get("tags") or [])


def _month_start(d: date, back: int) -> date:
    y, m = divmod(d.month - 1 - back, 12)
    return date(d.year + y, m + 1, 1)


def month_flows(splits: list[dict], today: date, months: int = MONTHS) -> list[dict]:
    """Entradas, gastos e sobra dos `months` meses fechados antes do mês de hoje (mais antigo primeiro)."""
    out = []
    for back in range(months, 0, -1):
        start = _month_start(today, back)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        ins = outs = 0.0
        n = 0
        for s in splits:
            if _is_internal(s):
                continue
            d = date.fromisoformat(str(s["date"])[:10])
            if not start <= d <= end:
                continue
            value = float(s.get("amount") or 0)
            if s.get("type") == "deposit":
                ins += value
            elif s.get("type") == "withdrawal":
                outs += value
            else:
                continue
            n += 1
        if n >= MIN_TXNS:
            out.append({"mes": start.strftime("%Y-%m"), "entradas": round(ins, 2), "gastos": round(outs, 2),
                        "sobra": round(ins - outs, 2), "lancamentos": n})
    return out


def flows_cached(firefly, today: date) -> list[dict]:
    """Uma leitura do Firefly por dia (relatório e gestor rodam na mesma manhã)."""
    cache = state.get("savings_flows") or {}
    if cache.get("dia") == today.isoformat():
        return cache["meses"]
    start = _month_start(today, MONTHS)
    splits = list(firefly.iter_splits(start, today.replace(day=1) - timedelta(days=1), "all"))
    flows = month_flows(splits, today)
    state.put("savings_flows", {"dia": today.isoformat(), "meses": flows})
    return flows


def plan(flows: list[dict], floor: float, rate: float, folga_30d: float | None, today: date) -> dict:
    """Meta do mês e o porquê, em números prontos para o relatório e o gestor."""
    rate = min(max(rate, 0.0), 1.0)
    typical = median(f["sobra"] for f in flows) if flows else None
    suggested = round(max(0.0, rate * typical), 2) if typical is not None else 0.0
    meta = max(suggested, floor)
    if typical is None:
        why = "sem 1 mês fechado com dados ainda, vale o mínimo definido"
    elif suggested >= floor:
        why = f"{rate:.0%} da sobra típica de {len(flows)} mês(es)"
    else:
        why = "a sobra típica está abaixo do mínimo definido; vale o mínimo"
    capped = False
    meta_mes = meta
    if folga_30d is not None:
        room = max(0.0, rate * folga_30d)
        if room < meta:
            meta_mes, capped = room, True
            why = "as contas dos próximos 30 dias apertam; guardar só o que cabe sem atrasar fatura"
    days = calendar.monthrange(today.year, today.month)[1]
    left = days - today.day + 1
    return {
        "meta_mes": round(meta_mes, 2),
        "meta_cheia": round(meta, 2),
        "sugerida": suggested,
        "piso": round(floor, 2),
        "taxa": rate,
        "sobra_tipica": round(typical, 2) if typical is not None else None,
        "meses": flows,
        "folga_30d": round(folga_30d, 2) if folga_30d is not None else None,
        "limitada_pela_folga": capped,
        "guardar_hoje": round(meta_mes / days, 2),
        "reservar_resto_do_mes": round(meta_mes * left / days, 2),
        "motivo": why,
    }
