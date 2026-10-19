"""Cliente do LLM local (Ollama no KaviPC, RTX 3060 Ti 8 GB, qwen2.5:7b-instruct Q4_K_M).

A GPU é dividida com o pokemon-radar (visão, rascunhos, treinos). Para um projeto
não travar o outro, toda chamada passa por três portões:

1. Janela (config/llm_janelas.json): espelha os períodos "cheio" da agenda da GPU
   do radar. Jobs em lote (`priority="lote"`) só usam a IA dentro da janela.
2. Modelo já carregado: fora da janela, o bot (`priority="agora"`) só usa a IA se o
   qwen já estiver na VRAM (GET /api/ps). Assim nunca força carregar 4,7 GB numa hora
   em que o radar está trabalhando ou você está jogando.
3. Trava (app/state.llm_lock): uma chamada por vez dentro deste container; o próprio
   Ollama enfileira as do radar.

Qualquer portão fechado => `None`, e quem chamou segue sem IA (regra 7 do CLAUDE.md).

Structured output: com um JSON Schema em `format`, o Ollama só gera texto que
obedece ao schema; a resposta vira dado validado, não texto livre.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, timezone

import requests

from app import state
from app.config import load_llm_windows

log = logging.getLogger(__name__)
BRT = timezone(timedelta(hours=-3))  # Brasil sem horário de verão desde 2019
DIAS = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(now: datetime | None = None) -> bool:
    now = (now or datetime.now(UTC)).astimezone(BRT)
    day, minute = DIAS[now.weekday()], now.hour * 60 + now.minute
    return any(day in w["dias"] and _minutes(w["inicio"]) <= minute < _minutes(w["fim"])
               for w in load_llm_windows().get("janelas", []))


class LocalLLM:
    def __init__(self, base_url: str, model: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._online: bool | None = None

    # ------------------------------------------------------------- portões
    def online(self) -> bool:
        if self._online is None:
            self._online = False
            if self.base_url:
                try:
                    self._online = requests.get(f"{self.base_url}/api/tags", timeout=3).ok
                except requests.RequestException:
                    pass
            if not self._online:
                log.info("LLM local fora do ar; seguindo sem IA")
        return self._online

    def resident(self) -> bool:
        try:
            models = requests.get(f"{self.base_url}/api/ps", timeout=3).json().get("models") or []
            return any(m.get("name") == self.model or m.get("model") == self.model for m in models)
        except (requests.RequestException, ValueError):
            return False

    def allowed(self, priority: str = "lote", now: datetime | None = None) -> bool:
        if not self.online():
            return False
        if in_window(now):
            return True
        return priority == "agora" and self.resident()

    # backwards compat com testes/clients antigos
    def available(self) -> bool:
        return self.allowed("lote")

    # -------------------------------------------------------------- chamadas
    def _chat(self, messages: list[dict], *, schema: dict | None, temperature: float, priority: str,
              tools: list[dict] | None = None) -> dict | None:
        if not self.allowed(priority):
            return None
        wait = 20 if priority == "agora" else 180
        with state.llm_lock(timeout=wait) as got:
            if not got:
                log.info("IA ocupada por outra tarefa deste container; seguindo sem IA")
                return None
            windows = load_llm_windows()
            body = {
                "model": self.model,
                "stream": False,
                "messages": messages,
                "options": {"temperature": temperature, "num_ctx": 8192},
                "keep_alive": windows.get("keep_alive_dentro" if in_window() else "keep_alive_fora", "2m"),
            }
            if schema:
                body["format"] = schema
            if tools:
                body["tools"] = tools
            try:
                resp = requests.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json().get("message") or {}
            except (requests.RequestException, ValueError) as exc:
                log.warning("LLM falhou: %s", exc)
                return None

    def chat_json(self, system: str, user: str, schema: dict, temperature: float = 0.0,
                  priority: str = "lote") -> dict | None:
        msg = self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         schema=schema, temperature=temperature, priority=priority)
        if not msg:
            return None
        try:
            return json.loads(msg.get("content") or "")
        except ValueError:
            return None

    def chat_text(self, system: str, user: str, temperature: float = 0.3, priority: str = "lote") -> str | None:
        msg = self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         schema=None, temperature=temperature, priority=priority)
        text = (msg or {}).get("content", "").strip()
        return text or None

    def chat_messages(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2,
                      priority: str = "agora") -> dict | None:
        """Conversa com ferramentas (tool calling): o modelo pode pedir para rodar uma função."""
        return self._chat(messages, schema=None, temperature=temperature, priority=priority, tools=tools)
