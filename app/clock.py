"""Relógio do sistema no horário de Brasília.

O container roda em UTC (Dockerfile). Com `date.today()` puro, das 21:00 às 23:59 o
"hoje" já era o dia seguinte: o /hoje da noite mostrava o mês e as contas do dia errado.
O Brasil não tem horário de verão desde 2019, então UTC-3 fixo basta (sem depender de tzdata).
O agendador continua em UTC (app/scheduler.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3), "BRT")


def now() -> datetime:
    return datetime.now(BRT)


def today() -> date:
    return now().date()
