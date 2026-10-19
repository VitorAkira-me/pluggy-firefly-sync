"""Matemática financeira determinística (o LLM nunca calcula; chama estas funções).

Conceitos:
- Juros compostos: o rendimento de cada período entra na base do próximo.
  VF = VP * (1 + i)^n.
- Taxa equivalente: 1% ao mês NÃO é 12% ao ano; é (1,01)^12 - 1 = 12,68% ao ano.
- Valor presente (VP): quanto vale hoje um dinheiro que só sai no futuro, descontado
  pelo que ele renderia até lá. É o que decide "à vista com desconto ou parcelado sem juros".
- Custo efetivo: taxa por mês que iguala o que você recebeu hoje ao que vai pagar depois.
"""
from __future__ import annotations

import math


def annual_to_monthly(rate_aa_pct: float) -> float:
    """% ao ano -> % ao mês equivalente (composto)."""
    return ((1 + rate_aa_pct / 100) ** (1 / 12) - 1) * 100


def monthly_to_annual(rate_am_pct: float) -> float:
    return ((1 + rate_am_pct / 100) ** 12 - 1) * 100


def future_value(present: float, rate_am_pct: float, months: int, monthly_deposit: float = 0.0) -> float:
    """Saldo após `months` meses aplicando `present` hoje e `monthly_deposit` no fim de cada mês."""
    i = rate_am_pct / 100
    if i == 0:
        return present + monthly_deposit * months
    return present * (1 + i) ** months + monthly_deposit * ((1 + i) ** months - 1) / i


def months_to_goal(goal: float, monthly_deposit: float, rate_am_pct: float, present: float = 0.0) -> int | None:
    """Meses até `goal` guardando `monthly_deposit` por mês. None se nunca chega."""
    if present >= goal:
        return 0
    if monthly_deposit <= 0 and rate_am_pct <= 0:
        return None
    i = rate_am_pct / 100
    if i == 0:
        return math.ceil((goal - present) / monthly_deposit)
    num = goal * i + monthly_deposit
    den = present * i + monthly_deposit
    if den <= 0:
        return None
    return math.ceil(math.log(num / den) / math.log(1 + i))


def present_value_installments(installment: float, n: int, rate_am_pct: float, first_in_months: int = 1) -> float:
    """Valor hoje de `n` parcelas iguais, a primeira daqui a `first_in_months` meses."""
    i = rate_am_pct / 100
    return sum(installment / (1 + i) ** (first_in_months + k) for k in range(n))


def cash_vs_installments(cash_price: float, installment: float, n: int, cdi_aa_pct: float) -> dict:
    """Compara pagar à vista com parcelar, supondo que o dinheiro não gasto rende o CDI.

    Retorna o VP das parcelas, a diferença e qual sai mais barato hoje."""
    rate = annual_to_monthly(cdi_aa_pct)
    pv = present_value_installments(installment, n, rate)
    diff = pv - cash_price
    return {
        "taxa_mes_pct": round(rate, 3),
        "total_parcelado": round(installment * n, 2),
        "vp_parcelado": round(pv, 2),
        "a_vista": round(cash_price, 2),
        "diferenca_hoje": round(diff, 2),
        "melhor": "parcelado" if diff < 0 else "à vista",
    }


def effective_monthly_cost(received: float, payments: list[float]) -> float | None:
    """Taxa mensal (%) que faz o VP de `payments` (1 por mês, começando no mês 1) igualar `received`.

    Ex.: Pix no crédito de R$ 121,91 pago em 2x de R$ 67,70 -> custo efetivo ao mês.
    Busca binária: o VP cai quando a taxa sobe, então dá pra achar a taxa exata."""
    if received <= 0 or not payments or sum(payments) <= received:
        return 0.0 if payments and sum(payments) <= received else None
    lo, hi = 0.0, 1.0  # 0% a 100% ao mês
    for _ in range(100):
        mid = (lo + hi) / 2
        pv = sum(p / (1 + mid) ** (k + 1) for k, p in enumerate(payments))
        if pv > received:
            lo = mid
        else:
            hi = mid
    return round(mid * 100, 2)


def real_rate(nominal_aa_pct: float, inflation_aa_pct: float) -> float:
    """Juro real (Fisher): (1 + nominal) / (1 + inflação) - 1. É o quanto o dinheiro cresce em poder de compra."""
    return ((1 + nominal_aa_pct / 100) / (1 + inflation_aa_pct / 100) - 1) * 100
