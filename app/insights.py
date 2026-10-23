"""Análise semanal dos gastos: fatos calculados em Python + texto do LLM local.

Divisão de trabalho (CLAUDE.md regra 6, "IA nunca faz conta"):
- Python calcula tudo: totais, comparações, estabelecimentos, recorrências
  prováveis (assinaturas) e gastos fora do padrão.
- O LLM recebe só esses fatos prontos em JSON e escreve 3 a 5 observações
  com dicas práticas. Se o LLM estiver desligado, sai um texto montado por
  regra com os mesmos fatos.

Uso: `python -m app.insights [--no-send] [--days 180]`.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import defaultdict
from datetime import date, timedelta

from app import clock
from app.clients.firefly import FireflyClient
from app.clients.llm import LocalLLM
from app.clients.telegram import send_message
from app.config import load_settings
from app.ml import merchant_key
from app.report import fmt_brl

log = logging.getLogger("insights")


def _d(s: dict) -> date:
    return date.fromisoformat(str(s["date"])[:10])


def _amt(s: dict) -> float:
    return float(s.get("amount") or 0)


def weekly_by_category(splits: list[dict], today: date, weeks: int = 8) -> list[dict]:
    """Últimos 7 dias vs média semanal das `weeks` semanas anteriores, por categoria."""
    week_start = today - timedelta(days=6)
    base_start = week_start - timedelta(days=7 * weeks)
    cur: dict[str, float] = defaultdict(float)
    base: dict[str, float] = defaultdict(float)
    for s in splits:
        d, cat = _d(s), s.get("category_name") or "Sem categoria"
        if week_start <= d <= today:
            cur[cat] += _amt(s)
        elif base_start <= d < week_start:
            base[cat] += _amt(s)
    rows = []
    for cat in set(cur) | set(base):
        avg = base[cat] / weeks
        rows.append({"categoria": cat, "semana": round(cur[cat], 2), "media_semanal": round(avg, 2),
                     "variacao_pct": round((cur[cat] - avg) / avg * 100, 1) if avg else None})
    return sorted(rows, key=lambda r: -r["semana"])


def month_to_date(splits: list[dict], today: date) -> dict:
    """Gasto do mês até hoje vs mesmo período do mês passado."""
    start = today.replace(day=1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    prev_cut = prev_start + timedelta(days=min(today.day, prev_end.day) - 1)
    cur = sum(_amt(s) for s in splits if start <= _d(s) <= today)
    prev = sum(_amt(s) for s in splits if prev_start <= _d(s) <= prev_cut)
    return {"mes_atual_ate_hoje": round(cur, 2), "mes_passado_mesmo_periodo": round(prev, 2),
            "variacao_pct": round((cur - prev) / prev * 100, 1) if prev else None}


def top_merchants(splits: list[dict], today: date, days: int = 30, n: int = 5) -> list[dict]:
    totals: dict[str, list[float]] = defaultdict(list)
    for s in splits:
        if _d(s) >= today - timedelta(days=days - 1):
            totals[merchant_key(s.get("description", "")) or "?"].append(_amt(s))
    rows = [{"estabelecimento": k, "total": round(sum(v), 2), "vezes": len(v)} for k, v in totals.items()]
    return sorted(rows, key=lambda r: -r["total"])[:n]


def probable_subscriptions(splits: list[dict], today: date) -> list[dict]:
    """Mesmo estabelecimento, valor parecido (±10%), em pelo menos 3 meses diferentes
    dos últimos 4: forte candidato a assinatura ou cobrança recorrente."""
    since = today - timedelta(days=120)
    by_m: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for s in splits:
        if _d(s) >= since:
            by_m[merchant_key(s.get("description", ""))].append((_d(s), _amt(s)))
    out = []
    for m, items in by_m.items():
        if not m or len(items) < 3:
            continue
        med = statistics.median(a for _, a in items)
        months = {(d.year, d.month) for d, a in items if abs(a - med) <= 0.10 * med}
        if len(months) >= 3:
            out.append({"estabelecimento": m, "valor_tipico": round(med, 2), "meses": len(months)})
    return sorted(out, key=lambda r: -r["valor_tipico"])


def outliers(splits: list[dict], today: date, days: int = 7) -> list[dict]:
    """Gastos recentes muito acima do normal da categoria (> Q3 + 1,5·IQR, regra de Tukey),
    só pra categorias com histórico suficiente (>= 8 transações)."""
    by_cat: dict[str, list[float]] = defaultdict(list)
    for s in splits:
        by_cat[s.get("category_name") or "Sem categoria"].append(_amt(s))
    limits = {}
    for cat, vals in by_cat.items():
        if len(vals) >= 8:
            q1, _, q3 = statistics.quantiles(vals, n=4)
            limits[cat] = q3 + 1.5 * (q3 - q1)
    out = []
    for s in splits:
        cat = s.get("category_name") or "Sem categoria"
        if _d(s) >= today - timedelta(days=days - 1) and cat in limits and _amt(s) > limits[cat]:
            out.append({"data": str(_d(s)), "descricao": (s.get("description") or "")[:60],
                        "valor": round(_amt(s), 2), "categoria": cat, "limite_normal": round(limits[cat], 2)})
    return sorted(out, key=lambda r: -r["valor"])[:5]


def build_facts(splits: list[dict], today: date) -> dict:
    return {
        "data": today.isoformat(),
        "mes": month_to_date(splits, today),
        "semana_por_categoria": weekly_by_category(splits, today)[:8],
        "maiores_estabelecimentos_30d": top_merchants(splits, today),
        "provaveis_assinaturas": probable_subscriptions(splits, today),
        "gastos_fora_do_padrao_7d": outliers(splits, today),
        "sem_categoria_30d": sum(1 for s in splits if _d(s) >= today - timedelta(days=29) and not s.get("category_name")),
    }


SYSTEM = (
    "Você é um consultor financeiro pessoal, direto e gentil, falando com o Akira em português do Brasil. "
    "Receberá FATOS em JSON já calculados. Escreva de 3 a 5 tópicos curtos (máx. 2 linhas cada), cada um "
    "com uma observação e uma ação prática. Use SOMENTE números que estão nos fatos; não calcule nada novo. "
    "Não use travessão. Não invente estabelecimentos. Comece direto pelos tópicos, com '•'."
)


def fallback_text(f: dict) -> str:
    lines = []
    m = f["mes"]
    if m["variacao_pct"] is not None:
        sentido = "acima" if m["variacao_pct"] > 0 else "abaixo"
        lines.append(f"• Mês até hoje: {fmt_brl(m['mes_atual_ate_hoje'])}, {abs(m['variacao_pct']):.0f}% {sentido} do mesmo período do mês passado.")
    for r in [r for r in f["semana_por_categoria"] if r["variacao_pct"] and r["variacao_pct"] > 50 and r["semana"] > 50][:2]:
        lines.append(f"• {r['categoria']}: {fmt_brl(r['semana'])} na semana, {r['variacao_pct']:.0f}% acima da sua média.")
    for o in f["gastos_fora_do_padrao_7d"][:2]:
        lines.append(f"• Fora do padrão: {o['descricao']} ({fmt_brl(o['valor'])}) em {o['categoria']}.")
    if f["sem_categoria_30d"]:
        lines.append(f"• {f['sem_categoria_30d']} transações sem categoria nos últimos 30 dias.")
    return "\n".join(lines) or "• Semana tranquila, nada fora do padrão."


def main(send: bool = True, days: int = 180) -> str:
    s = load_settings()
    ff = FireflyClient(s.firefly_base_url, s.firefly_token)
    today = clock.today()
    from app.facts import spending
    splits = spending(list(ff.iter_splits(today - timedelta(days=days), today, "withdrawal")))
    facts = build_facts(splits, today)
    llm = LocalLLM(s.llm_base_url, s.llm_model, s.llm_timeout)
    body = llm.chat_text(SYSTEM, json.dumps(facts, ensure_ascii=False)) if s.llm_enabled else None
    origin = f"IA local ({s.llm_model})" if body else "regras"
    text = f"🧠 *Análise da semana*\n\n{body or fallback_text(facts)}\n\n_Gerado por {origin}; números calculados pelo sistema._"
    print(text)
    if send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--days", type=int, default=180)
    a = p.parse_args()
    main(send=not a.no_send, days=a.days)
