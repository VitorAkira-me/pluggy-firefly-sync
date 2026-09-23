"""Agendador interno, roda como processo principal do container (substitui o cron do sistema).

Por que isso existe: o `cron` do Debian (pacote `cron`, usado antes) roda cada
job com um ambiente praticamente vazio (só HOME, LOGNAME, PATH, PWD, SHELL) —
ele NÃO herda as variáveis de ambiente que o Docker/Portainer injeta no
processo principal do container (PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET,
FIREFLY_TOKEN, TELEGRAM_BOT_TOKEN etc). Isso foi confirmado ao vivo em
23/09/2026: um job de teste (`env | cut -d= -f1 > arquivo`) disparado pelo
cron mostrou só 5 variáveis, nenhuma delas relacionada à Pluggy/Firefly/
Telegram — por isso todo `sync.py`/`daily_report.py` disparado pelo cron
falhava com `400 Bad Request` na Pluggy (`clientId`/`clientSecret` vazios),
mesmo o `python3` sendo encontrado certinho (o bug do PATH, resolvido antes,
era um problema diferente e já estava corrigido).

A correção "tradicional" seria exportar as variáveis pro cron (escrever um
arquivo com `export VAR=valor` e fazer cada job dar `source` nele antes de
rodar) — mas isso significa gravar os segredos em um arquivo à parte dentro
do container, o que preferimos evitar. Esse script resolve o problema pela
raiz: roda como o próprio processo principal do container (`CMD` no
Dockerfile), que é exatamente onde o Docker injeta as variáveis de ambiente
— o mesmo motivo pelo qual rodar `python3 sync.py` manualmente no console
sempre funcionou. Sem cron, sem exportar nada, sem arquivo de segredo extra:
as variáveis já estão no `os.environ` deste processo, herdadas normalmente
pelos `subprocess.run(...)` que ele dispara.
"""
import subprocess
import sys
import time
from datetime import datetime, timezone


def run(script: str, logfile: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(logfile, "a") as f:
        f.write(f"\n=== {timestamp} — iniciando {script} ===\n")
        f.flush()
        subprocess.run(
            [sys.executable, script],
            cwd="/app",
            stdout=f,
            stderr=subprocess.STDOUT,
        )


def main() -> None:
    print("scheduler.py iniciado — sync.py a cada 3h (00,03,06...UTC), daily_report.py às 7h UTC", flush=True)
    last_sync_key = None
    last_report_key = None
    while True:
        now = datetime.now(timezone.utc)

        sync_key = (now.date(), now.hour) if now.hour % 3 == 0 else None
        if sync_key is not None and sync_key != last_sync_key:
            run("sync.py", "/app/logs/sync.log")
            last_sync_key = sync_key

        report_key = now.date() if now.hour == 7 else None
        if report_key is not None and report_key != last_report_key:
            run("daily_report.py", "/app/logs/report.log")
            last_report_key = report_key

        time.sleep(30)


if __name__ == "__main__":
    main()
