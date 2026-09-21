"""Cliente mínimo para a API do Firefly III.

Docs: https://api-docs.firefly-iii.org/
"""
from datetime import date

import requests


class FireflyClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(f"{self.base_url}{path}", headers=self.headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_account(self, account_id: int) -> dict:
        return self._get(f"/api/v1/accounts/{account_id}")["data"]

    def find_transaction_by_external_id(self, external_id: str) -> dict | None:
        """Evita duplicar: procura uma transação já importada com esse id da Pluggy.

        TODO: se seu volume de transações crescer muito, troque essa busca por
        um pequeno banco local (sqlite) de ids já sincronizados — é mais rápido
        que perguntar pro Firefly a cada transação.
        """
        data = self._get("/api/v1/search/transactions", {"query": f'external_id_is:"{external_id}"'})
        results = data.get("data", [])
        return results[0] if results else None

    def create_transaction(
        self,
        *,
        type_: str,
        date_: str,
        amount: str,
        description: str,
        source_id: int | None = None,
        source_name: str | None = None,
        destination_id: int | None = None,
        destination_name: str | None = None,
        category_name: str | None = None,
        external_id: str,
    ) -> dict:
        split = {
            "type": type_,
            "date": date_,
            "amount": amount,
            "description": description or "(sem descrição)",
            "external_id": external_id,
        }
        if source_id is not None:
            split["source_id"] = source_id
        elif source_name:
            split["source_name"] = source_name
        if destination_id is not None:
            split["destination_id"] = destination_id
        elif destination_name:
            split["destination_name"] = destination_name
        if category_name:
            split["category_name"] = category_name

        return self._post("/api/v1/transactions", {"error_if_duplicate_hash": True, "transactions": [split]})

    def list_recurring_due_this_month(self) -> list:
        """Usado pelo relatório diário para saber quais contas fixas ainda vão vencer."""
        data = self._get("/api/v1/recurrences")
        today = date.today()
        due = []
        for rec in data.get("data", []):
            attrs = rec["attributes"]
            for repetition in attrs.get("repetitions", []):
                # Só sabemos interpretar recorrência mensal simples (moment = dia do mês).
                # "weekly"/"yearly"/"ndom" usam outro formato de moment — ignorados por ora.
                if repetition.get("type") != "monthly":
                    continue
                try:
                    day = int(repetition.get("moment", ""))
                except ValueError:
                    continue
                if day >= today.day:
                    for txn in attrs.get("transactions", []):
                        due.append({
                            "title": attrs.get("title"),
                            "day": day,
                            "amount": float(txn.get("amount", 0)),
                        })
        return due
