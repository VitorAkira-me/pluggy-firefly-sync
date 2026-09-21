FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Agenda: sincroniza a cada 3h, relatório diário às 7h.
# O .env é lido pelo próprio script (python-dotenv), então não precisa
# exportar variável de ambiente pro cron separadamente.
#
# IMPORTANTE: cron roda os jobs com um PATH mínimo próprio (normalmente
# /usr/bin:/bin), que NÃO inclui /usr/local/bin — é onde o python3 desta
# imagem (python:3.11-slim) realmente mora. Sem a linha PATH= abaixo, todo
# job agendado falha silenciosamente com "python3: not found" mesmo com o
# cron rodando certinho no horário certo — um teste manual pelo console
# interativo (que usa o PATH completo do shell) não pega esse bug, porque
# o problema é só no ambiente que o cron usa pra rodar os jobs, não no
# comando em si. Foi exatamente isso que aconteceu aqui: o cron disparava
# no horário, mas cada execução falhava antes de importar qualquer coisa.
RUN printf 'PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin\n0 */3 * * * cd /app && python3 sync.py >> /app/logs/sync.log 2>&1\n0 7 * * * cd /app && python3 daily_report.py >> /app/logs/report.log 2>&1\n' > /etc/cron.d/pluggy-sync \
    && chmod 0644 /etc/cron.d/pluggy-sync \
    && crontab /etc/cron.d/pluggy-sync \
    && mkdir -p /app/logs \
    && touch /app/logs/sync.log /app/logs/report.log

# Roda o cron em primeiro plano (é o que mantém o container vivo) e
# acompanha os logs, pra aparecerem em `docker logs` / no Portainer.
CMD cron && tail -f /app/logs/sync.log /app/logs/report.log
