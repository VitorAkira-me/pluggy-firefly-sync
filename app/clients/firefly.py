"""Cliente mínimo da API oficial do Firefly III (https://api-docs.firefly-iii.org/)."""
from __future__ import annotations

import calendar
from collections import Counter
from collections.abc import Iterator
from datetime import date, timedelta

import requests


def fingerprint(account_id, date_: str, amount, description, key) -> tuple:
    return (str(account_id), date_[:10], f"{abs(float(amount or 0)):.2f}", key(description or ""))


def next_occurrence(day: int, today: date) -> date:
    """Próxima data (>= hoje) com esse dia do mês. Dia 31 em mês de 30 vira dia 30."""
    year, month = today.year, today.month
    candidate = date(year, month, min(day, calendar.monthrange(year, month)[1]))
    if candidate >= today:
        return candidate
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


class FireflyClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = session or requests.Session()
        self.http.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
        )

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.http.get(f"{self.base_url}{path}", params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _paged(self, path: str, params: dict | None = None) -> Iterator[dict]:
        page = 1
        while True:
            data = self._get(path, {**(params or {}), "page": page, "limit": 200})
            yield from data.get("data", [])
            if page >= data.get("meta", {}).get("pagination", {}).get("total_pages", 1):
                return
            page += 1

    # ---------------------------------------------------------- transações
    def iter_splits(self, start: date, end: date, type_: str = "all") -> Iterator[dict]:
        """Cada split de transação no período, já achatado com o id do grupo."""
        for txn in self._paged("/api/v1/transactions", {"start": start.isoformat(), "end": end.isoformat(), "type": type_}):
            for split in txn["attributes"]["transactions"]:
                yield {"group_id": txn["id"], **split}

    def existing_external_ids(self, start: date, end: date) -> set[str]:
        """Ids externos já gravados no período. Uma consulta paginada por execução do
        sync, em vez de uma busca por transação (era o gargalo no Raspberry Pi)."""
        return self.existing_index(start, end)[0]

    def existing_index(self, start: date, end: date) -> tuple[set[str], Counter]:
        """(ids externos, contagem de "impressões digitais") das transações do período.

        A impressão digital (conta, data, valor, estabelecimento) pega a mesma transação mesmo
        quando a Pluggy troca os ids (banco reconectado): sem ela, o histórico duplicaria."""
        from app.ml import merchant_key
        ids: set[str] = set()
        prints: Counter = Counter()
        for s in self.iter_splits(start, end):
            if s.get("external_id"):
                ids.add(s["external_id"])
            acc = s.get("source_id") if s.get("type") == "withdrawal" else s.get("destination_id")
            prints[fingerprint(acc, str(s.get("date"))[:10], s.get("amount"), s.get("description"), merchant_key)] += 1
        return ids, prints

    def create_transaction(
        self,
        *,
        type_: str,
        date_: str,
        amount: str,
        description: str,
        external_id: str,
        source_id: int | None = None,
        source_name: str | None = None,
        destination_id: int | None = None,
        destination_name: str | None = None,
        category_name: str | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
    ) -> dict:
        split = {
            "type": type_,
            "date": date_,
            "amount": amount,
            "description": description or "(sem descrição)",
            "external_id": external_id,
        }
        optional = {
            "source_id": source_id,
            "source_name": None if source_id is not None else source_name,
            "destination_id": destination_id,
            "destination_name": None if destination_id is not None else destination_name,
            "category_name": category_name,
            "tags": tags,
            "notes": notes,
        }
        split.update({k: v for k, v in optional.items() if v})
        resp = self.http.post(
            f"{self.base_url}/api/v1/transactions",
            json={"error_if_duplicate_hash": True, "transactions": [split]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def update_split(self, group_id: str, journal_id: str, **fields) -> dict:
        resp = self.http.put(
            f"{self.base_url}/api/v1/transactions/{group_id}",
            json={"transactions": [{"transaction_journal_id": journal_id, **fields}]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------- leitura
    def list_category_names(self) -> list[str]:
        return sorted(c["attributes"]["name"] for c in self._paged("/api/v1/categories"))

    def list_piggy_banks(self) -> list[dict]:
        out = []
        for p in self._paged("/api/v1/piggy-banks"):
            a = p["attributes"]
            out.append({"id": p["id"], "name": a.get("name"), "target": float(a.get("target_amount") or 0),
                        "current": float(a.get("current_amount") or 0), "target_date": a.get("target_date")})
        return out

    def list_budgets(self) -> list[dict]:
        return [{"id": b["id"], "name": b["attributes"].get("name"),
                 "monthly": float(b["attributes"].get("auto_budget_amount") or 0)} for b in self._paged("/api/v1/budgets")]

    def find_account_id(self, name: str, type_: str = "asset") -> int | None:
        for a in self._paged("/api/v1/accounts", {"type": type_}):
            if a["attributes"].get("name", "").lower() == name.lower():
                return int(a["id"])
        return None

    def list_recurring_events(self, days_ahead: int = 30, today: date | None = None) -> list[dict]:
        """Próximas ocorrências (janela de `days_ahead` dias contando hoje) das
        recorrências MENSAIS do Firefly, com direction "expense" ou "income"."""
        today = today or date.today()
        horizon = today + timedelta(days=days_ahead - 1)
        events = []
        for rec in self._paged("/api/v1/recurrences"):
            attrs = rec["attributes"]
            if attrs.get("active") is False:
                continue
            for repetition in attrs.get("repetitions", []):
                if repetition.get("type") != "monthly":
                    continue
                try:
                    occ = next_occurrence(int(repetition.get("moment", "")), today)
                except ValueError:
                    continue
                if occ > horizon:
                    continue
                for txn in attrs.get("transactions", []):
                    if txn.get("destination_type") == "Expense account":
                        direction = "expense"
                    elif txn.get("source_type") == "Revenue account":
                        direction = "income"
                    else:
                        continue
                    events.append(
                        {
                            "title": attrs.get("title"),
                            "date": occ.isoformat(),
                            "amount": float(txn.get("amount", 0)),
                            "direction": direction,
                            "source_id": int(txn["source_id"]) if txn.get("source_id") else None,
                        }
                    )
        return events
