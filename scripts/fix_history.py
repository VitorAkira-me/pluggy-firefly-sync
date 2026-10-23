"""Corrige o histórico já gravado no Firefly com as regras novas (out/2026).

O que faz (só nas transações importadas da Pluggy, external_id "pluggy:..."):
1. marca movimentações internas (pagamento de fatura, Pix no crédito, PIX entre contas
   próprias, aplicação automática) com a categoria "Transferência interna" e a tag "interno";
2. espalha parcelas que vieram todas na data da compra (ex.: 10x no mesmo dia) para o mês
   em que cada uma pesa, com a tag "parcela";
3. aplica as regras (config/category_rules.json e as do bot) no que está sem categoria, saídas e entradas.

Por padrão só MOSTRA o plano. Para gravar: `python scripts/fix_history.py --apply`.
Rodar de novo é seguro (o que já está certo é ignorado).
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clock  # noqa: E402
from app.classify import rule_matches, user_rules  # noqa: E402
from app.clients.firefly import FireflyClient  # noqa: E402
from app.config import load_account_mapping, load_category_rules, load_settings  # noqa: E402
from app.normalize import (  # noqa: E402
    INSTALLMENT_TAG,
    INTERNAL_CATEGORY,
    INTERNAL_TAG,
    detect_internal,
    shift_installments,
)


def as_pluggy_like(split: dict, card_ids: set[int]) -> tuple[str, dict, dict]:
    """Converte um split do Firefly no formato (conta, alvo, txn) que o normalize entende."""
    is_withdrawal = split.get("type") == "withdrawal"
    acc = int(split["source_id"] if is_withdrawal else split["destination_id"])
    is_card = acc in card_ids
    amount = float(split["amount"])
    signed = -amount if is_withdrawal else amount          # do ponto de vista da conta
    pluggy_amount = -signed if is_card else signed          # Pluggy inverte o sinal no cartão
    txn = {"id": str(split["transaction_journal_id"]), "amount": pluggy_amount, "date": str(split["date"])[:10],
           "description": split.get("description") or ""}
    return str(acc), {"is_credit_card": is_card}, txn


def plan(splits: list[dict], card_ids: set[int]) -> list[tuple[dict, dict, str]]:
    batch = [as_pluggy_like(s, card_ids) for s in splits]
    internal = detect_internal(batch)
    shifts = shift_installments(batch)
    rules = user_rules() + load_category_rules()
    changes = []
    for s in splits:
        jid = str(s["transaction_journal_id"])
        tags = list(s.get("tags") or [])
        upd: dict = {}
        why = []
        if jid in internal and s.get("category_name") != INTERNAL_CATEGORY:
            upd["category_name"] = INTERNAL_CATEGORY
            upd["tags"] = [t for t in tags if t != "revisar"] + ([INTERNAL_TAG] if INTERNAL_TAG not in tags else [])
            why.append(f"interno ({internal[jid]})")
        elif jid not in internal and not s.get("category_name"):
            desc, value = s.get("description") or "", float(s.get("amount") or 0)
            expense = s.get("type") == "withdrawal"
            rule = next((r for r in rules if rule_matches(r, desc, value, expense)), None)
            if rule and s.get("type") in ("withdrawal", "deposit"):
                upd["category_name"] = rule["category"]
                why.append(f"regra '{rule['match']}'")
        sh = shifts.get(jid)
        if sh and sh["date"] != str(s["date"])[:10]:
            upd["date"] = sh["date"]
            upd["tags"] = (upd.get("tags") or tags) + ([INSTALLMENT_TAG] if INSTALLMENT_TAG not in tags else [])
            why.append(f"parcela {sh['n']}/{sh['total']} -> {sh['date']}")
        if upd:
            changes.append((s, upd, "; ".join(why)))
    return changes


def main(apply: bool, days: int) -> None:
    s = load_settings()
    ff = FireflyClient(s.firefly_base_url, s.firefly_token)
    card_ids = {m["firefly_account_id"] for m in load_account_mapping().values() if m.get("is_credit_card")}
    today = clock.today()
    splits = [x for x in ff.iter_splits(today - timedelta(days=days), today + timedelta(days=400), "all")
              if str(x.get("external_id") or "").startswith("pluggy:") and x.get("type") in ("withdrawal", "deposit")]
    changes = plan(splits, card_ids)
    for sp, _upd, why in changes:
        print(f"{str(sp['date'])[:10]} R$ {float(sp['amount']):>9.2f} {sp.get('description', '')[:40]:<40} -> {why}")
    print(f"\n{len(changes)} transações a corrigir de {len(splits)} analisadas.")
    if not apply:
        print("Nada foi gravado. Para aplicar: python scripts/fix_history.py --apply")
        return
    ok = 0
    for sp, upd, _ in changes:
        try:
            ff.update_split(str(sp["group_id"]), str(sp["transaction_journal_id"]), **upd)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"falhou {sp['transaction_journal_id']}: {exc}")
    print(f"{ok} corrigidas.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--days", type=int, default=120)
    a = p.parse_args()
    main(a.apply, a.days)
