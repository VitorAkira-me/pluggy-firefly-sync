"""Cliente mínimo da API da Pluggy (credenciais do Meu Pluggy). Só leitura.

Limitações conhecidas da Pluggy (não são bug nosso):
- Não existe "listar todos os itens"; os item_id vêm do Dashboard.
- O GET /transactions antigo responde 410; usamos /v2/transactions (cursor).
- No plano gratuito não dá pra forçar atualização; a Pluggy atualiza sozinha.
- O campo `balance` de cartão muda de significado conforme o banco: em alguns
  conectores inclui parcelas futuras (ver docs/operacao.md).
"""
from __future__ import annotations

from datetime import date, timedelta

import requests

API_BASE = "https://api.pluggy.ai"


class PluggyAuthError(RuntimeError):
    """A Pluggy recusou PLUGGY_CLIENT_ID/PLUGGY_CLIENT_SECRET (a mensagem nunca traz os valores)."""


class PluggyClient:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = session or requests.Session()
        self._api_key: str | None = None

    def _auth(self) -> str:
        if not self._api_key:
            resp = self.http.post(
                f"{API_BASE}/auth",
                json={"clientId": self.client_id, "clientSecret": self.client_secret},
                timeout=30,
            )
            if resp.status_code in (400, 401, 403):
                raise PluggyAuthError(
                    f"Pluggy recusou as credenciais (HTTP {resp.status_code}). Confira PLUGGY_CLIENT_ID e "
                    "PLUGGY_CLIENT_SECRET no Portainer (dashboard.pluggy.ai > aplicação > credenciais)."
                )
            resp.raise_for_status()
            self._api_key = resp.json()["apiKey"]
        return self._api_key

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.http.get(f"{API_BASE}{path}", headers={"X-API-KEY": self._auth()}, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_item(self, item_id: str) -> dict:
        return self._get(f"/items/{item_id}")

    def list_accounts(self, item_id: str) -> list[dict]:
        return self._get("/accounts", {"itemId": item_id})["results"]

    def get_account(self, account_id: str) -> dict:
        """Conta com o saldo real do banco (campo `balance`)."""
        return self._get(f"/accounts/{account_id}")

    def list_bills(self, account_id: str) -> list[dict]:
        """Faturas de cartão já FECHADAS (a aberta não aparece aqui)."""
        return self._get("/bills", {"accountId": account_id}).get("results", [])

    def list_transactions(self, account_id: str, since_days: int = 7) -> list[dict]:
        date_from = (date.today() - timedelta(days=since_days)).isoformat()
        results: list[dict] = []
        path, params = "/v2/transactions", {"accountId": account_id, "dateFrom": date_from}
        while True:
            data = self._get(path, params)
            results.extend(data["results"])
            next_qs = data.get("next")
            if not next_qs:
                return results
            path, params = "/v2/transactions" + next_qs, None
