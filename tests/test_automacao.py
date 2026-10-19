"""Fatura calculada sozinha, saúde das conexões, mapeamento fora do Git e bot só do dono."""
import json
from datetime import UTC, date, datetime

from app import invoices, report, state
from app.bot import Bot
from app.health import check_item

TODAY = date(2026, 10, 19)


def t(id_, amount, d, desc):
    return {"id": id_, "amount": amount, "date": d, "description": desc}


def test_ciclo_pelas_faturas_fechadas():
    bills = [{"dueDate": "2026-09-22", "billClosingDate": "2026-09-15"}, {"dueDate": "2026-10-22", "billClosingDate": "2026-10-15"}]
    c = invoices.cycle_from_bills(bills, TODAY, None)
    assert (c["last_close"], c["next_close"], c["next_due"]) == (date(2026, 10, 15), date(2026, 11, 15), date(2026, 11, 22))


def test_ciclo_sem_faturas_usa_dia_de_vencimento():
    c = invoices.cycle_from_bills([], TODAY, 4)
    assert c["next_due"] == date(2026, 11, 4) and c["fonte"] == "dia de vencimento"


def test_ciclo_avanca_quando_ultima_fatura_fechada_ja_passou():
    bills = [{"dueDate": "2026-09-22", "billClosingDate": "2026-09-15"}]
    c = invoices.cycle_from_bills(bills, TODAY, None)
    assert (c["last_close"], c["next_close"], c["next_due"]) == (
        date(2026, 10, 15),
        date(2026, 11, 15),
        date(2026, 11, 22),
    )


def test_fatura_aberta_soma_compras_parcela_do_ciclo_e_tira_pagamento():
    txns = [
        t("a", 100.0, "2026-10-16", "LOJA A"),                      # no ciclo
        t("b", 50.0, "2026-10-10", "LOJA ANTIGA"),                  # ciclo anterior
        t("c", -100.0, "2026-10-17", "PAGAMENTO COM SALDO"),        # pagamento: fora
        t("d", -20.0, "2026-10-18", "ESTORNO LOJA A"),              # estorno: abate
    ] + [t(f"m{n}", 190.0, "2026-09-26", f"MISPLEI J{n:02d}/10") for n in range(1, 11)]  # só a 2/10 (26/10) cai aqui
    assert invoices.open_amount(txns, date(2026, 10, 15), date(2026, 11, 15)) == 270.0


def test_cartao_dividido_e_valor_do_bot_vence_quando_fecha():
    est = {"amount": 5000.0, "my_amount": 2500.0, "due_date": "2026-11-04", "last_close": "2026-09-28", "share": 0.5}
    black = {"firefly_account_name": "Bradesco Black", "shared": True}
    item = report.card_item(None, "x", black, TODAY, est)
    assert item["amount"] == 2500.0 and item["total"] == 5000.0 and item["source"] == "calculada"
    state.set_manual_invoice("Bradesco Black", 2300, my_share=True)           # informado hoje, depois do fechamento 28/09
    item = report.card_item(None, "x", black, TODAY, est)
    assert item["amount"] == 2300.0 and item["source"] == "bot"
    est_next = {**est, "last_close": "2026-10-28"}                            # fechou depois do aviso: volta ao cálculo
    assert report.card_item(None, "x", black, date(2026, 10, 30), est_next)["source"] == "calculada"


def test_saude_detecta_login_atraso_e_consentimento():
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    ok = {"connector": {"name": "Itaú"}, "status": "UPDATED", "lastUpdatedAt": "2026-10-19T02:00:00Z",
          "nextAutoSyncAt": "2026-10-20T02:00:00Z"}
    assert check_item(ok, now) == []
    bad = {"connector": {"name": "Nubank"}, "status": "LOGIN_ERROR", "lastUpdatedAt": "2026-10-16T02:00:00Z",
           "nextAutoSyncAt": None, "consentExpiresAt": "2026-10-25T00:00:00Z"}
    msgs = " ".join(check_item(bad, now))
    assert "LOGIN_ERROR" in msgs and "82 h" in msgs and "desligada" in msgs and "vence em 5" in msgs


def test_mapeamento_pela_variavel_de_ambiente(monkeypatch):
    from app import config
    monkeypatch.setenv("ACCOUNT_MAPPING_JSON", json.dumps({"mappings": [
        {"pluggy_account_id": "env-1", "firefly_account_id": 1, "firefly_account_name": "X", "is_credit_card": False}]}))
    config.load_account_mapping.cache_clear()
    assert list(config.load_account_mapping()) == ["env-1"]
    config.load_account_mapping.cache_clear()


def test_bot_so_responde_ao_dono_em_chat_privado(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    bot = Bot()
    assert bot.authorized({"id": 111, "type": "private"}, {"id": 111})
    assert not bot.authorized({"id": 111, "type": "group"}, {"id": 111})        # grupo nunca
    assert not bot.authorized({"id": 111, "type": "private"}, {"id": 222})      # outro usuário
    assert not bot.authorized({"id": 999, "type": "private"}, {"id": 999})      # outro chat
