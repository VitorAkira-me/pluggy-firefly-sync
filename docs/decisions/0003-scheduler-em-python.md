# 0003. Agendamento por processo Python, não cron

**Status:** aceita (set/2026, reafirmada out/2026)

O cron do Debian roda jobs sem as variáveis de ambiente do Docker e com PATH mínimo.
Quebrou duas vezes. `app.scheduler` é o processo principal (recebe as variáveis) e
dispara cada job em subprocesso com timeout.
