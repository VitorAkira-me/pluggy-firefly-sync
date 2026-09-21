"""Busca transações novas na Pluggy e cria no Firefly III, sem duplicar.

Uso:
    python sync.py                  # sincroniza normalmente
    python sync.py --list-accounts  # só lista as contas da Pluggy (pra preencher account_mapping.json)
"""
import sys

from config import guess_category, load_account_mapping, load_settings
from firefly_client import FireflyClient
from pluggy_client import PluggyClient


def list_accounts(pluggy: PluggyClient) -> None:
    for item in pluggy.list_items():
        print(f"Item (conexão) {item['id']} — {item.get('connector', {}).get('name')}")
        for acc in pluggy.list_accounts(item["id"]):
            print(f"  conta {acc['id']} — {acc['name']} ({acc['type']}/{acc['subtype']})")


def sync() -> None:
    settings = load_settings()
    mapping = load_account_mapping()
    pluggy = PluggyClient(settings.pluggy_client_id, settings.pluggy_client_secret)
    firefly = FireflyClient(settings.firefly_base_url, settings.firefly_token)

    created, skipped, unmapped = 0, 0, 0

    for pluggy_account_id, target in mapping.items():
        if pluggy_account_id == "PREENCHER":
            continue
        for txn in pluggy.list_transactions(pluggy_account_id):
            external_id = f"pluggy:{txn['id']}"

            if firefly.find_transaction_by_external_id(external_id):
                skipped += 1
                continue

            amount = float(txn["amount"])
            description = txn.get("description") or txn.get("descriptionRaw") or "(sem descrição)"
            rule = guess_category(description)

            # Na Pluggy, valor negativo = saída da conta (despesa); positivo = entrada (receita).
            # Isso é o padrão pra contas correntes; cartão de crédito costuma vir invertido —
            # TODO: confirme o sinal assim que os primeiros dados reais chegarem, e ajuste aqui
            # se notar despesas do cartão aparecendo como receita (ou vice-versa).
            if amount < 0:
                type_ = "withdrawal"
                source_id = target["firefly_account_id"]
                destination_name = rule.get("expense_account") or "(a categorizar)"
                destination_id = None
            else:
                type_ = "deposit"
                destination_id = target["firefly_account_id"]
                source_name = "(a categorizar)"
                source_id = None

            try:
                if type_ == "withdrawal":
                    firefly.create_transaction(
                        type_=type_,
                        date_=txn["date"][:10],
                        amount=f"{abs(amount):.2f}",
                        description=description,
                        source_id=source_id,
                        destination_name=destination_name,
                        category_name=rule.get("category"),
                        external_id=external_id,
                    )
                else:
                    firefly.create_transaction(
                        type_=type_,
                        date_=txn["date"][:10],
                        amount=f"{abs(amount):.2f}",
                        description=description,
                        source_name=source_name,
                        destination_id=destination_id,
                        category_name=rule.get("category"),
                        external_id=external_id,
                    )
                created += 1
                if not rule:
                    unmapped += 1
            except Exception as exc:  # noqa: BLE001 — queremos seguir sincronizando o resto mesmo se uma falhar
                print(f"[sync] Falhou ao criar transação {external_id} ({description}): {exc}")

    print(f"Sincronização concluída: {created} criadas, {skipped} já existiam, {unmapped} sem categoria reconhecida.")


if __name__ == "__main__":
    if "--list-accounts" in sys.argv:
        s = load_settings()
        list_accounts(PluggyClient(s.pluggy_client_id, s.pluggy_client_secret))
    else:
        sync()
