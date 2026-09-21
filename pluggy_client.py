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

    def get_item(self, item_id: str) -> dict:
        """Busca um item (conexão com um banco) pelo id.

        A Pluggy não oferece um "listar todos os itens" por segurança (a doc
        oficial é explícita sobre isso: "Listing existing connections is not
        provided due to security reasons"). Por isso não existe um
        list_items() — o item_id precisa ser copiado do Dashboard
        (dashboard.pluggy.ai → aplicação → Itens Conectados) uma vez, e a
        partir daí o próprio script guarda esse id.
        """
        return self._get(f"/items/{item_id}")

    def list_accounts(self, item_id: str | None = None) -> list:
        params = {"itemId": item_id} if item_id else {}
        return self._get("/accounts", params)["results"]

    def list_transactions(self, account_id: str, since_days: int = 7) -> list:
        """Traz transações dos últimos N dias (padrão 7, cobre folgas de sincronização).

        Usa /v2/transactions (paginação por cursor). O antigo GET /transactions
        (paginação por página, "from"/"page"/"pageSize") foi descontinuado pela
        Pluggy — passou a responder 410 Gone. A doc oficial já mostra ele como
        "List by Page (deprecated)" e recomenda o /v2/transactions no lugar.
        """
        date_from = (date.today() - timedelta(days=since_days)).isoformat()
        results = []
        path = "/v2/transactions"
        params = {"accountId": account_id, "dateFrom": date_from}
        while True:
            data = self._get(path, params)
            results.extend(data["results"])
            next_qs = data.get("next")
            if not next_qs:
                break
            # "next" já vem como query string pronta: GET /v2/transactions{next}
            path = "/v2/transactions" + next_qs
            params = None
        return results
