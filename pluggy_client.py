"""Cliente mínimo para a API da Pluggy (via credenciais do Meu Pluggy).

Docs: https://docs.pluggy.ai/
"""
from datetime import date, timedelta

import requests

API_BASE = "https://api.pluggy.ai"


class PluggyClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._api_key = None

    def _auth(self) -> str:
        if self._api_key:
            return self._api_key
        resp = requests.post(
            f"{API_BASE}/auth",
            json={"clientId": self.client_id, "clientSecret": self.client_secret},
            timeout=30,
        )
        resp.raise_for_status()
        self._api_key = resp.json()["apiKey"]
        return self._api_key

    def _get(self, path: str, params: dict | None = None) -> dict:
        headers = {"X-API-KEY": self._auth()}
        resp = requests.get(f"{API_BASE}{path}", headers=headers, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_items(self) -> list:
        """Cada 'item' é uma conexão com um banco (ex: Itaú, Bradesco)."""
        return self._get("/items")["results"]

    def list_accounts(self, item_id: str | None = None) -> list:
        params = {"itemId": item_id} if item_id else {}
        return self._get("/accounts", params)["results"]

    def list_transactions(self, account_id: str, since_days: int = 7) -> list:
        """Traz transações dos últimos N dias (padrão 7, cobre folgas de sincronização)."""
        date_from = (date.today() - timedelta(days=since_days)).isoformat()
        results = []
        page = 1
        while True:
            data = self._get(
                "/transactions",
                {"accountId": account_id, "from": date_from, "page": page, "pageSize": 500},
            )
            results.extend(data["results"])
            if page >= data.get("totalPages", 1):
                break
            page += 1
        return results
