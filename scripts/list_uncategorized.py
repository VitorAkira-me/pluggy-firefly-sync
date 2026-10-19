"""Lista gastos sem categoria (ou marcados com a tag "revisar") no Firefly.

Uso no Console do container: `python scripts/list_uncategorized.py --days 30`
Cole a saída no chat dizendo a categoria dos que se repetem; vira regra em
config/category_rules.json (ou corrija direto no Firefly, que o histórico aprende).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.firefly import FireflyClient  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.ml import merchant_key  # noqa: E402


def main(days: int) -> None:
    s = load_settings()
    ff = FireflyClient(s.firefly_base_url, s.firefly_token)
    today = date.today()
    rows = [
        sp for sp in ff.iter_splits(today - timedelta(days=days), today, "withdrawal")
        if not sp.get("category_name") or "revisar" in (sp.get("tags") or [])
    ]
    if not rows:
        print(f"Nada sem categoria nos últimos {days} dias.")
        return
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(merchant_key(r.get("description", "")) or "?", []).append(r)
    print(f"{len(rows)} gastos sem categoria em {len(groups)} estabelecimentos (últimos {days} dias):\n")
    for key, items in sorted(groups.items(), key=lambda kv: -sum(float(i["amount"]) for i in kv[1])):
        total = sum(float(i["amount"]) for i in items)
        print(f"- {key or '?'}: {len(items)}x, R$ {total:.2f} | ex.: \"{items[0].get('description')}\"")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    main(p.parse_args().days)
