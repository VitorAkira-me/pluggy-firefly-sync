"""Processo principal do container: agendador e bot do Telegram lado a lado.

Duas threads independentes:
- scheduler: dispara sync, revisão, manhã (resumo + gestor) e análise semanal, cada job
  num subprocesso com timeout;
- bot: escuta o Telegram e responde comandos, botões e perguntas.
Se uma cair, a outra continua; o container só reinicia se as duas morrerem.
"""
from __future__ import annotations

import logging
import threading
import time

from app import scheduler
from app.bot import Bot


def _guard(name: str, target) -> threading.Thread:
    def run():
        while True:
            try:
                target()
            except Exception:  # noqa: BLE001
                logging.exception("%s caiu; reiniciando em 30 s", name)
                time.sleep(30)
    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    return t


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    threads = [_guard("scheduler", scheduler.loop), _guard("bot", lambda: Bot().run_forever())]
    while any(t.is_alive() for t in threads):
        time.sleep(60)


if __name__ == "__main__":
    main()
