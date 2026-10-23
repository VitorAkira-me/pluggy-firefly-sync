"""Dados de mercado atualizados (Banco Central, API SGS) com histórico, para o gestor ensinar com números reais.

Séries (oficiais, gratuitas, JSON):
  432    Selic meta (% a.a.)
  4389   CDI anualizado base 252 (% a.a.)
  433    IPCA mensal (%)
  13522  IPCA acumulado 12 meses (%)

Conceito de "ciclo": o Copom sobe a Selic para frear a inflação e corta quando ela
cede. Comparar a Selic de hoje com a de 6 e 12 meses atrás mostra em que fase do
ciclo estamos, e isso muda o que faz sentido (ex.: renda fixa pós-fixada rende mais
com Selic alta; financiamento fica mais caro).

Cache de 12 h no estado (/data) para não bater no BCB a cada mensagem do bot.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import requests

from app import clock, state
from app.finmath import real_rate

log = logging.getLogger(__name__)
BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
HEADERS = {"User-Agent": "personal-finance/1.0 (uso pessoal)", "Accept": "application/json"}
# ultimos/N aceita no máximo 20 pontos (acima disso a API responde 400); a Selic diária usa janela de datas
SERIES = {"selic": (432, "janela", 400), "cdi": (4389, "ultimos", 20), "ipca_mes": (433, "ultimos", 20),
          "ipca_12m": (13522, "ultimos", 20)}
TTL = 12 * 3600


def _fetch(code: int, mode: str, n: int) -> list[tuple[str, float]]:
    if mode == "janela":
        end = clock.today()
        url = BASE.format(code=code) + f"?formato=json&dataInicial={(end - timedelta(days=n)):%d/%m/%Y}&dataFinal={end:%d/%m/%Y}"
    else:
        url = BASE.format(code=code) + f"/ultimos/{min(n, 20)}?formato=json"
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return [(i["data"], float(i["valor"])) for i in resp.json()]
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(3)
    return []


def history(force: bool = False) -> dict:
    cached = state.get("market_history")
    if cached and not force and time.time() - cached.get("ts", 0) < TTL:
        return cached
    data = {"ts": time.time()}
    for name, (code, mode, n) in SERIES.items():
        try:
            data[name] = _fetch(code, mode, n)
        except Exception as exc:  # noqa: BLE001 (uma série fora do ar não derruba as outras)
            log.warning("BCB série %s falhou: %s", code, exc)
            data[name] = (cached or {}).get(name, [])
    state.put("market_history", data)
    return data


def _value_ago(series: list[tuple[str, float]], days: int) -> float | None:
    """Valor da série `days` dias antes do último ponto (séries diárias como a Selic meta)."""
    if not series:
        return None
    last = datetime.strptime(series[-1][0], "%d/%m/%Y")
    for d, v in reversed(series):
        if (last - datetime.strptime(d, "%d/%m/%Y")).days >= days:
            return v
    return series[0][1]


def snapshot(force: bool = False) -> dict:
    """Fotografia didática: níveis atuais, ciclo da Selic e juro real."""
    h = history(force)
    out: dict = {}
    if h.get("selic"):
        now = h["selic"][-1][1]
        ago6, ago12 = _value_ago(h["selic"], 182), _value_ago(h["selic"], 365)
        out["selic_aa"] = now
        out["selic_6m_atras"] = ago6
        out["selic_12m_atras"] = ago12
        ref = ago6 if ago6 is not None else now
        out["ciclo_selic"] = "alta" if now > ref + 0.1 else "queda" if now < ref - 0.1 else "estável"
    if h.get("cdi"):
        out["cdi_aa"] = h["cdi"][-1][1]
    if h.get("ipca_mes"):
        out["ipca_mes"] = h["ipca_mes"][-1][1]
        out["ipca_mes_ref"] = h["ipca_mes"][-1][0][3:]
        out["ipca_6m"] = [v for _, v in h["ipca_mes"][-6:]]
    if h.get("ipca_12m"):
        out["ipca_12m"] = h["ipca_12m"][-1][1]
    if "cdi_aa" in out and "ipca_12m" in out:
        out["juro_real_aa"] = round(real_rate(out["cdi_aa"], out["ipca_12m"]), 2)
    return out
