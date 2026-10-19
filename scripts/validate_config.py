"""Valida os arquivos de config/ (roda no CI a cada push/PR).

Pega erro de edição antes que o Portainer faça redeploy com um arquivo quebrado.
"""
import datetime
import json
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"
errors: list[str] = []


def load(name: str):
    try:
        return json.loads((CONFIG / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"{name} inválido: {exc}")


# ---- account_mapping.json
mapping_path = "account_mapping.json"
if not (CONFIG / mapping_path).exists():
    mapping_path = "account_mapping.example.json"
mappings = load(mapping_path).get("mappings")
if not isinstance(mappings, list) or not mappings:
    sys.exit(f"{mapping_path}: 'mappings' precisa ser uma lista não vazia")
seen = set()
for i, m in enumerate(mappings):
    label = m.get("firefly_account_name", f"mappings[{i}]")
    for key, typ in (("pluggy_account_id", str), ("firefly_account_id", int), ("firefly_account_name", str), ("is_credit_card", bool)):
        if not isinstance(m.get(key), typ) or isinstance(m.get(key), bool) != (typ is bool):
            errors.append(f"{label}: '{key}' ausente ou com tipo errado")
    if m.get("pluggy_account_id") in seen:
        errors.append(f"{label}: pluggy_account_id duplicado (duplicaria transações)")
    seen.add(m.get("pluggy_account_id"))
    inv = m.get("manual_invoice")
    if inv is None:
        continue
    if not m.get("is_credit_card"):
        errors.append(f"{label}: 'manual_invoice' só vale pra cartão de crédito")
    amount, day = inv.get("amount"), inv.get("day")
    if isinstance(amount, bool) or not isinstance(amount, int | float) or amount < 0:
        errors.append(f"{label}: manual_invoice.amount precisa ser número >= 0")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        errors.append(f"{label}: manual_invoice.day precisa ser inteiro entre 1 e 31")
    try:
        datetime.date.fromisoformat(inv.get("updated_at", ""))
    except (TypeError, ValueError):
        errors.append(f"{label}: manual_invoice.updated_at precisa ser data AAAA-MM-DD")

# ---- category_rules.json
rules = load("category_rules.json").get("rules")
if not isinstance(rules, list):
    errors.append("category_rules.json: 'rules' precisa ser lista")
else:
    for i, r in enumerate(rules):
        if not isinstance(r.get("match"), str) or not r["match"].strip():
            errors.append(f"category_rules.json[{i}]: 'match' vazio")
        if not isinstance(r.get("category"), str) or not r["category"].strip():
            errors.append(f"category_rules.json[{i}]: 'category' vazio")

if errors:
    sys.exit("config inválida:\n- " + "\n- ".join(errors))
print(f"config ok ({len(mappings)} contas, {len(rules)} regras)")
