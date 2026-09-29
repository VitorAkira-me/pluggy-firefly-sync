"""Valida account_mapping.json (roda no GitHub Actions a cada push/PR que mexe nele).

Pega erro de edicao automatica (ex: lembrete da fatura do Bradesco Black) antes
que o Portainer faca redeploy com um arquivo quebrado.
"""
import datetime
import json
import sys
from pathlib import Path

path = Path(__file__).resolve().parent.parent / "account_mapping.json"
errors = []

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"account_mapping.json invalido: {exc}")

mappings = data.get("mappings")
if not isinstance(mappings, list) or not mappings:
    sys.exit("account_mapping.json: 'mappings' precisa ser uma lista nao vazia")

for i, m in enumerate(mappings):
    label = m.get("firefly_account_name", f"mappings[{i}]")
    for key, typ in (("pluggy_account_id", str), ("firefly_account_id", int), ("is_credit_card", bool)):
        if not isinstance(m.get(key), typ):
            errors.append(f"{label}: '{key}' ausente ou com tipo errado")
    inv = m.get("manual_invoice")
    if inv is None:
        continue
    if not m.get("is_credit_card"):
        errors.append(f"{label}: 'manual_invoice' so vale pra cartao de credito")
    amount = inv.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
        errors.append(f"{label}: manual_invoice.amount precisa ser numero >= 0")
    day = inv.get("day")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        errors.append(f"{label}: manual_invoice.day precisa ser inteiro entre 1 e 31")
    try:
        datetime.date.fromisoformat(inv.get("updated_at", ""))
    except (TypeError, ValueError):
        errors.append(f"{label}: manual_invoice.updated_at precisa ser data YYYY-MM-DD")

if errors:
    sys.exit("account_mapping.json invalido:\n- " + "\n- ".join(errors))
print(f"account_mapping.json ok ({len(mappings)} contas)")
