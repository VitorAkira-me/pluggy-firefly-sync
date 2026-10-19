"""Busca transações novas na Pluggy, limpa, classifica e grava no Firefly III sem duplicar.

Uso:
    python -m app.sync                               # sincroniza
    python -m app.sync --dry-run                     # mostra o que faria, sem gravar
    python -m app.sync --list-accounts <item_id>...  # descobrir ids pro account_mapping.json

Etapas de cada execução:
1. busca o lote de todas as contas (últimos SYNC_SINCE_DAYS dias);
2. limpa o lote (app/normalize.py): movimentações internas e parcelas;
3. pula o que já está no Firefly (1 consulta paginada, não 1 por transação);
4. classifica o resto (regra -> histórico -> ML; o LLM fica para a passada da madrugada,
   app/review.py, para não disputar a GPU com o pokemon-radar fora da janela);
5. grava.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from app.classify import REVIEW_TAG, Classifier, Decision
from app.clients.firefly import FireflyClient
from app.clients.llm import LocalLLM
from app.clients.pluggy import PluggyClient
from app.config import Settings, load_account_mapping, load_category_rules_file, load_settings
from app.normalize import INSTALLMENT_TAG, INTERNAL_CATEGORY, INTERNAL_TAG, detect_internal, shift_installments, signed_amount

log = logging.getLogger("sync")
HISTORY_DAYS = 365


def to_firefly_payload(txn: dict, target: dict, decision: Decision, *, internal_reason: str | None = None,
                       installment: dict | None = None) -> dict:
    amount = signed_amount(txn, target)
    description = txn.get("description") or txn.get("descriptionRaw") or "(sem descrição)"
    tags: list[str] = []
    notes = [decision.note()]
    category = decision.category
    expense_account = decision.expense_account
    if internal_reason:
        category, expense_account = INTERNAL_CATEGORY, "Movimentação interna"
        tags.append(INTERNAL_TAG)
        notes = [f"interno: {internal_reason}"]
    elif decision.needs_review:
        tags.append(REVIEW_TAG)
    if installment:
        tags.append(INSTALLMENT_TAG)
        notes.append(f"parcela {installment['n']}/{installment['total']}")
    payload = {
        "date_": (installment or {}).get("date") or txn["date"][:10],
        "amount": f"{abs(amount):.2f}",
        "description": description,
        "external_id": f"pluggy:{txn['id']}",
        "category_name": category,
        "notes": " | ".join(notes),
        "tags": tags or None,
    }
    if amount < 0:
        payload.update(type_="withdrawal", source_id=target["firefly_account_id"],
                       destination_name=expense_account or "(a categorizar)")
    else:
        payload.update(type_="deposit", destination_id=target["firefly_account_id"],
                       source_name="Movimentação interna" if internal_reason else "(a categorizar)")
    return payload


def build_classifier(firefly: FireflyClient, settings: Settings, use_llm: bool = False) -> Classifier:
    today = date.today()
    history = [s for s in firefly.iter_splits(today - timedelta(days=HISTORY_DAYS), today, "withdrawal")
               if s.get("category_name") != INTERNAL_CATEGORY]
    llm = LocalLLM(settings.llm_base_url, settings.llm_model, settings.llm_timeout) if (use_llm and settings.llm_enabled) else None
    return Classifier(history, allowed_categories(firefly), llm)


def allowed_categories(firefly: FireflyClient) -> list[str]:
    """Categorias que o LLM pode escolher: as do Firefly + a lista canônica do config."""
    names = set(firefly.list_category_names()) | set(load_category_rules_file().get("categories", []))
    return sorted(names - {INTERNAL_CATEGORY})


def fetch_batch(pluggy: PluggyClient, mapping: dict, since_days: int) -> list[tuple[str, dict, dict]]:
    return [(pid, target, txn) for pid, target in mapping.items() for txn in pluggy.list_transactions(pid, since_days)]


def sync(dry_run: bool = False) -> dict:
    settings = load_settings()
    mapping = load_account_mapping()
    pluggy = PluggyClient(settings.pluggy_client_id, settings.pluggy_client_secret)
    firefly = FireflyClient(settings.firefly_base_url, settings.firefly_token)

    today = date.today()
    batch = fetch_batch(pluggy, mapping, settings.sync_since_days)
    internal = detect_internal(batch)
    installments = shift_installments(batch)
    # parcelas futuras podem ter sido gravadas meses à frente: a janela de ids conhecidos cobre até 4 anos
    known = firefly.existing_external_ids(today - timedelta(days=settings.sync_since_days + 3), today + timedelta(days=1460))
    classifier = build_classifier(firefly, settings, use_llm=False)

    created = skipped = failed = 0
    for _, target, txn in batch:
        external_id = f"pluggy:{txn['id']}"
        if external_id in known:
            skipped += 1
            continue
        desc = txn.get("description") or txn.get("descriptionRaw") or ""
        amount = signed_amount(txn, target)
        if txn["id"] in internal:
            decision = Decision(INTERNAL_CATEGORY, None, "interno", 1.0, internal[txn["id"]])
        else:
            decision = classifier.classify(desc, abs(amount), txn["date"][:10], expense=amount < 0)
        payload = to_firefly_payload(txn, target, decision, internal_reason=internal.get(txn["id"]),
                                     installment=installments.get(txn["id"]))
        if dry_run:
            print(f"[dry-run] {payload['date_']} {payload['type_']} R$ {payload['amount']} | {desc} -> {payload['notes']}")
            created += 1
            continue
        try:
            firefly.create_transaction(**payload)
            known.add(external_id)
            created += 1
        except Exception as exc:  # noqa: BLE001 (uma falha não pode parar o resto)
            failed += 1
            log.error("falhou %s (%s): %s", external_id, desc, exc)

    result = {"created": created, "skipped": skipped, "failed": failed, "internal": len(internal),
              "installments_shifted": sum(1 for v in installments.values() if v), "by_stage": dict(classifier.stats)}
    print(f"Sync: {created} criadas, {skipped} já existiam, {failed} falharam, {len(internal)} internas no lote | "
          f"etapas: {result['by_stage']}")
    return result


def list_accounts(item_ids: list[str]) -> None:
    s = load_settings()
    pluggy = PluggyClient(s.pluggy_client_id, s.pluggy_client_secret)
    for item_id in item_ids:
        item = pluggy.get_item(item_id)
        print(f"Item {item['id']} - {item.get('connector', {}).get('name')}")
        for acc in pluggy.list_accounts(item["id"]):
            print(f"  conta {acc['id']} - {acc['name']} ({acc['type']}/{acc['subtype']})")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-accounts", nargs="*")
    args = parser.parse_args()
    if args.list_accounts is not None:
        list_accounts(args.list_accounts)
    else:
        sync(dry_run=args.dry_run)
