"""Trilha de educação financeira do gestor (app/knowledge/*.md).

Por que uma base escrita e não "treinar" o modelo: ajustar (fine-tune) um modelo de 7B
com 8 GB de VRAM até dá (QLoRA), mas ele decoraria fatos que mudam (Selic, preços) e
continuaria podendo inventar. Com RAG (retrieval-augmented generation) o modelo recebe,
a cada mensagem, o trecho certo da base + os números do dia, e só explica. A base é
texto revisável: corrigir uma aula é editar um .md.

Escolha da aula do dia: primeiro uma que combine com algo que aconteceu (gatilhos:
Pix no crédito, parcelas, saldo zerado...), senão a próxima da trilha; nunca repete
uma aula dada nos últimos 21 dias.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import state

DIR = Path(__file__).resolve().parent / "knowledge"
REPEAT_DAYS = 21


@dataclass
class Lesson:
    slug: str
    title: str
    triggers: list[str]
    body: str

    def question(self) -> str:
        m = re.search(r"Pergunta para pensar:\s*(.+)", self.body)
        q = m.group(1).strip() if m else ""
        return q[:1].upper() + q[1:]

    def text_without_question(self) -> str:
        return re.sub(r"\n*Pergunta para pensar:.*", "", self.body, flags=re.S).strip()


def load_all() -> list[Lesson]:
    out = []
    for path in sorted(DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
        meta, body = (m.group(1), m.group(2)) if m else ("", raw)
        title = re.search(r"titulo:\s*(.+)", meta)
        trig = re.search(r"gatilhos:\s*\[(.*?)\]", meta)
        out.append(Lesson(path.stem, title.group(1).strip() if title else path.stem,
                          [t.strip() for t in (trig.group(1) if trig else "").split(",") if t.strip()], body.strip()))
    return out


def recently_taught(days: int = REPEAT_DAYS) -> set[str]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {e["slug"] for e in state.recent_events("aula", 60) if e["at"] >= cutoff}


def pick(active_triggers: set[str], lessons: list[Lesson] | None = None, taught: set[str] | None = None) -> Lesson:
    lessons = lessons or load_all()
    taught = recently_taught() if taught is None else taught
    fresh = [lesson for lesson in lessons if lesson.slug not in taught] or lessons
    specific = active_triggers - {"sempre"}  # gatilho de algo que aconteceu vence a trilha normal
    for lesson in fresh:
        if specific & set(lesson.triggers):
            return lesson
    return fresh[0]


def by_slug_or_topic(text: str) -> Lesson | None:
    text = text.lower()
    for lesson in load_all():
        if text in lesson.slug or text in lesson.title.lower() or text in lesson.triggers:
            return lesson
    return None


def mark_taught(lesson: Lesson) -> None:
    state.log_event("aula", {"slug": lesson.slug, "titulo": lesson.title})
