FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs \
    && touch /app/logs/sync.log /app/logs/report.log /app/logs/scheduler.log

# Agendamento: sync.py a cada 3h, daily_report.py às 7h — feito pelo próprio
# scheduler.py (loop Python), não pelo cron do sistema.
#
# HISTÓRICO (pra não reintroduzir o mesmo bug): a versão anterior usava o
# `cron` do Debian, agendado via /etc/cron.d. Isso teve DOIS bugs, achados e
# corrigidos em sessões separadas:
#   1) cron roda os jobs com um PATH mínimo (normalmente /usr/bin:/bin), que
#      não inclui /usr/local/bin, onde mora o python3 desta imagem — todo
#      job falhava com "python3: not found". Corrigido com uma linha PATH=
#      explícita no crontab.
#   2) Mesmo com o PATH corrigido, cron roda os jobs com um ambiente quase
#      vazio (só HOME, LOGNAME, PATH, PWD, SHELL) — ele NÃO herda as
#      variáveis que o Docker/Portainer injetam no processo principal do
#      container (PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET, FIREFLY_TOKEN,
#      TELEGRAM_BOT_TOKEN etc). Confirmado ao vivo em 23/09/2026 com um job
#      de teste que só via 5 variáveis de ambiente — por isso toda execução
#      agendada falhava com "400 Bad Request" na Pluggy (client id/secret
#      vazios), mesmo testes manuais pelo console (que usam o ambiente
#      completo do container) sempre funcionando.
#
# scheduler.py resolve o problema pela raiz: ele É o processo principal do
# container (CMD abaixo), que é exatamente onde o Docker injeta as
# variáveis de ambiente — o mesmo motivo pelo qual os testes manuais sempre
# funcionaram. Sem cron, sem exportar nada pra um arquivo à parte.
CMD ["python3", "scheduler.py"]
