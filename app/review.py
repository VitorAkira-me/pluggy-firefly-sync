"""Passada da madrugada: tenta classificar com IA o que ficou com a tag "revisar" e monta
a fila de revisão do bot com sugestões.

Roda às 03:00 de Brasília (dentro da janela da GPU, ver config/llm_janelas.json). O que a
IA classificar com confiança >= 0,80 é aplicado no Firefly (categoria + nota, tag removida);
o resto vai para a fila do /revisar com até 3 sugestões (histórico, ML e a dica da IA).

Uso: `python -m app.review [--no-llm]`.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from app import clock, state
from app.classify import REVIEW_TAG
from app.clients.firefly import FireflyClient
from app.config import load_settings
from app.sync import build_classifier

log = logging.getLogger("review")
LOOKBACK_DAYS = 60


def pending(ff: FireflyClient, today: date) -> list[dict]:
    return [s for s in ff.iter_splits(today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=400), "withdrawal")
            if REVIEW_TAG in (s.get("tags") or [])]


def remove_tag(tags: list[str] | None, tag: str) -> list[str]:
    return [t for t in (tags or []) if t != tag]


def apply_category(ff: FireflyClient, item: dict, category: str, note: str) -> None:
    notes = ((item.get("notes") or "") + f" | {note}").strip(" |")
    ff.update_split(str(item["group_id"]), str(item["transaction_journal_id"]), category_name=category,
                    tags=remove_tag(item.get("tags"), REVIEW_TAG), notes=notes)


def run(use_llm: bool = True) -> dict:
    s = load_settings()
    ff = FireflyClient(s.firefly_base_url, s.firefly_token)
    today = clock.today()
    items = pending(ff, today)
    classifier = build_classifier(ff, s, use_llm=use_llm)
    applied, queue = 0, []
    for it in items:
        desc = it.get("description") or ""
        d = classifier.classify(desc, float(it.get("amount") or 0), str(it.get("date"))[:10])
        if d.category:
            try:
                apply_category(ff, it, d.category, d.note())
                applied += 1
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("falhou ao aplicar %s: %s", it.get("transaction_journal_id"), exc)
        sugg = classifier.suggestions(desc)
        if d.stage == "revisar" and "IA sugere" in d.reason:
            hint = d.reason.split("IA sugere ", 1)[1].split(" (", 1)[0]
            sugg = [hint] + [x for x in sugg if x != hint]
        queue.append({"group_id": str(it["group_id"]), "journal_id": str(it["transaction_journal_id"]),
                      "desc": desc[:60], "amount": float(it.get("amount") or 0), "date": str(it.get("date"))[:10],
                      "tags": it.get("tags") or [], "notes": it.get("notes") or "", "sugestoes": sugg[:3]})
    state.put("review_queue", queue)
    print(f"Revisão: {applied} classificadas automaticamente, {len(queue)} na fila do /revisar | etapas {dict(classifier.stats)}")
    return {"applied": applied, "queued": len(queue)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-llm", action="store_true")
    run(use_llm=not p.parse_args().no_llm)
