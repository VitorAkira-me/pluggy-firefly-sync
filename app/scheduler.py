"""Agendador (roda numa thread do app.main, ao lado do bot). Substitui o cron.

Por que não cron: o cron do Debian roda os jobs com ambiente quase vazio e não herda
as variáveis do Docker/Portainer. Já quebrou duas vezes; na segunda (24/09/2026) o
sistema ficou parado quase 4 semanas sem ninguém perceber. Por isso, além de agendar,
este módulo avisa no Telegram quando um job falha 2 vezes seguidas.

Cada job roda num subprocesso com timeout: se travar, o agendador e o bot seguem.

Horários (UTC; Brasília = UTC-3; configuráveis por variável):
- app.sync      a cada SYNC_EVERY_HOURS (3)
- app.review    REVIEW_HOUR_UTC (6 = 03:00), dentro da janela da GPU
- app.report    REPORT_HOUR_UTC (8 = 05:00), números do dia
- app.advisor   logo depois do report: gestor (análise + aula)
- app.insights  INSIGHTS_WEEKDAY_UTC às INSIGHTS_HOUR_UTC (domingo 12 = 09:00)
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime

from app import state
from app.config import load_settings

JOB_TIMEOUT = 20 * 60
ORDER = ["app.sync", "app.review", "app.report", "app.advisor", "app.insights"]


def run(module: str) -> int:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== {stamp} iniciando {module}", flush=True)
    try:
        code = subprocess.run([sys.executable, "-m", module], timeout=JOB_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        print(f"=== {module} passou de {JOB_TIMEOUT // 60} min e foi interrompido", flush=True)
        code = 124
    print(f"=== {module} terminou com código {code}", flush=True)
    health = state.get("job_health", {}) or {}
    h = health.get(module, {"falhas": 0})
    h["falhas"] = 0 if code == 0 else h.get("falhas", 0) + 1
    h["ultimo"] = stamp
    h["codigo"] = code
    health[module] = h
    state.put("job_health", health)
    if h["falhas"] == 2:
        alert(f"⚠️ {module} falhou 2 vezes seguidas (código {code}). Veja os logs do container no Portainer.")
    return code


def alert(text: str) -> None:
    try:
        from app.clients.telegram import send_message
        s = load_settings()
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    except Exception as exc:  # noqa: BLE001
        print(f"alerta não enviado: {exc}", flush=True)


def due_jobs(now: datetime, s, last: dict) -> list[str]:
    keys = {
        "app.sync": (now.date(), now.hour) if now.hour % s.sync_every_hours == 0 else None,
        "app.review": now.date() if now.hour == getattr(s, "review_hour_utc", 6) else None,
        "app.report": now.date() if now.hour == s.report_hour_utc else None,
        "app.advisor": now.date() if now.hour == s.report_hour_utc else None,
        "app.insights": now.date() if (now.weekday() == s.insights_weekday_utc and now.hour == s.insights_hour_utc) else None,
    }
    jobs = []
    for job, key in keys.items():
        if key is not None and key != last.get(job):
            last[job] = key
            jobs.append(job)
    return sorted(jobs, key=ORDER.index)


def loop() -> None:
    s = load_settings()
    print(
        f"scheduler: sync a cada {s.sync_every_hours}h, revisão {s.review_hour_utc}h UTC, manhã {s.report_hour_utc}h UTC, "
        f"semanal dia {s.insights_weekday_utc} {s.insights_hour_utc}h UTC, IA local {'configurada' if s.llm_enabled else 'desligada'}",
        flush=True,
    )
    last: dict = {}
    while True:
        for job in due_jobs(datetime.now(UTC), s, last):
            run(job)
        time.sleep(30)


if __name__ == "__main__":
    loop()
