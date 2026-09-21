"""Busca transações novas na Pluggy e cria no Firefly III, sem duplicar.

Uso:
    python sync.py                                    # sincroniza normalmente
    python sync.py --list-accounts <item_id> [...]     # lista as contas de um ou mais itens
                                                        # (pra preencher account_mapping.json)

Sobre o --list-accounts: a Pluggy não tem endpoint pra "listar todos os
itens da aplicação" (é proposital, por segurança — ver pluggy_client.py).
Então o(s) item_id precisa(m) vir do Dashboard: dashboard.pluggy.ai →
sua aplicação → "Itens Conectados" → clica no item → o id aparece no
topo da tela de detalhes (ex: 6e9e93cd-4438-498c-b0ba-f8ba...). Copia um
id por banco conectado e passa todos na linha de comando.
"""
import sys

from config import guess_category, load_account_mapping, load_settings
from firefly_client import FireflyClient
from pluggy_client import PluggyClient


def list_accounts(pluggy: PluggyClient, item_ids: list[str]) -> None:
    if not item_ids:
        print("Uso: python sync.py --list-accounts <item_id> [<item_id> ...]")
        print("Os item_id vêm do Dashboard (dashboard.pluggy.ai → aplicação → Itens Conectados).")
        return
    for item_id in item_ids:
        item = pluggy.get_item(item_id)
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
            # Isso é o padrão pra contas correntes. Cartão de crédito vem invertido: positivo é
            # compra (aumenta a dívida), negativo é pagamento/estorno (reduz a dívida). Confirmado
            # com dados reais em 21/09/2026 (compras de cartão chegavam como positivas e viravam
            # "deposit" errado). account_mapping.json marca cada conta com "is_credit_card" pra
            # sabermos quando inverter o sinal antes de decidir o tipo da transação.
            if target.get("is_credit_card"):
                amount = -amount

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
        idx = sys.argv.index("--list-accounts")
        item_ids = sys.argv[idx + 1:]
        list_accounts(PluggyClient(s.pluggy_client_id, s.pluggy_client_secret), item_ids)
    else:
        sync()
