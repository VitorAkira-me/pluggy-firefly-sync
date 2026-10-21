"""Idempotência e conversão Pluggy -> Firefly."""
from datetime import date

import app.sync as sync_mod
from app.classify import Decision


class FakePluggy:
    def __init__(self, *a, **k):
        pass

    def list_transactions(self, account_id, since_days=7):
        today = date.today().isoformat()
        if account_id == "chk-1":
            return [
                {"id": "t1", "amount": -42.5, "date": today, "description": "IFOOD *RESTAURANTE"},
                {"id": "t2", "amount": 2400.0, "date": today, "description": "SALARIO PERNAMBUCANAS"},
            ]
        return [{"id": "t3", "amount": 120.87, "date": today, "description": "CLAUDE.AI SUBSCRIPTION"}]


class FakeFirefly:
    store: list = []

    def __init__(self, *a, **k):
        pass

    def existing_index(self, start, end):
        from collections import Counter

        from app.clients.firefly import fingerprint
        from app.ml import merchant_key
        prints = Counter(
            fingerprint(p.get("source_id") if p["type_"] == "withdrawal" else p.get("destination_id"),
                        p["date_"], p["amount"], p["description"], merchant_key)
            for p in FakeFirefly.store)
        return {p["external_id"] for p in FakeFirefly.store}, prints

    def iter_splits(self, start, end, type_="all"):
        return iter([])

    def list_category_names(self):
        return ["Alimentação", "Assinaturas"]

    def create_transaction(self, **payload):
        FakeFirefly.store.append(payload)


def run(monkeypatch):
    monkeypatch.setattr(sync_mod, "PluggyClient", FakePluggy)
    monkeypatch.setattr(sync_mod, "FireflyClient", FakeFirefly)
    return sync_mod.sync()


def test_mesma_transacao_importada_duas_vezes_grava_uma(monkeypatch):
    FakeFirefly.store = []
    first = run(monkeypatch)
    second = run(monkeypatch)
    assert first["created"] == 3
    assert second["created"] == 0 and second["skipped"] == 3
    assert len(FakeFirefly.store) == 3


def test_cartao_inverte_sinal_e_conta_corrente_nao(monkeypatch):
    FakeFirefly.store = []
    run(monkeypatch)
    by_id = {p["external_id"]: p for p in FakeFirefly.store}
    assert by_id["pluggy:t1"]["type_"] == "withdrawal" and by_id["pluggy:t1"]["source_id"] == 31
    assert by_id["pluggy:t2"]["type_"] == "deposit" and by_id["pluggy:t2"]["destination_id"] == 31
    assert by_id["pluggy:t3"]["type_"] == "withdrawal" and by_id["pluggy:t3"]["source_id"] == 5
    assert by_id["pluggy:t3"]["amount"] == "120.87"


def test_payload_marca_revisao_quando_sem_categoria():
    target = {"firefly_account_id": 31, "is_credit_card": False}
    d = Decision(None, None, "revisar", 0.0, "nada bateu")
    p = sync_mod.to_firefly_payload({"id": "x", "amount": -10, "date": "2026-10-01", "description": "LOJA X"}, target, d)
    assert p["tags"] == ["revisar"] and p["category_name"] is None
    assert "sem categoria" in p["notes"]


def test_pluggy_troca_ids_nao_duplica(monkeypatch):
    """Banco reconectado: mesmas transações com ids novos não podem entrar de novo."""
    FakeFirefly.store = []
    run(monkeypatch)
    original = FakePluggy.list_transactions

    def ids_novos(self, account_id, since_days=7):
        return [{**t, "id": "novo-" + t["id"]} for t in original(self, account_id, since_days)]

    monkeypatch.setattr(FakePluggy, "list_transactions", ids_novos)
    r = sync_mod.sync()
    assert r["created"] == 0 and r["same_content"] == 3 and len(FakeFirefly.store) == 3
