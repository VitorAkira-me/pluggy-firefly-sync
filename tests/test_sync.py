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

    def existing_external_ids(self, start, end):
        return {p["external_id"] for p in FakeFirefly.store}

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
