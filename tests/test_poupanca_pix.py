"""Meta de poupança calculada pelas contas, pagamento de fatura no cartão, regras de entrada e BCB parcial."""
from datetime import date

from app import invoices, price_signals, savings
from app.classify import rule_matches
from app.normalize import detect_internal
from app.report import tightest_margin

TODAY = date(2026, 10, 21)
CARD = {"is_credit_card": True}


def sp(d, amount, type_="withdrawal", cat=None, tags=None):
    return {"date": d, "amount": amount, "type": type_, "category_name": cat, "tags": tags or []}


def month(prefix, income, spent):
    """1 entrada e 5 gastos iguais num mês (o mínimo para o mês contar)."""
    return [sp(f"{prefix}-05", income, "deposit")] + [sp(f"{prefix}-1{i}", spent / 5) for i in range(5)]


# ------------------------------------------------------------- fatura do cartão
def test_pag_boleto_no_cartao_nao_zera_a_fatura():
    txns = [
        {"id": "p1", "amount": -5952.39, "date": "2026-10-05", "description": "PAG BOLETO BANCARIO"},
        {"id": "c1", "amount": 731.67, "date": "2026-10-04", "description": "TANGERINA GASTRO BAR"},
        {"id": "c2", "amount": 433.34, "date": "2026-10-07", "description": "JIM.COM* ESPACO"},
        {"id": "e1", "amount": -33.34, "date": "2026-10-08", "description": "ESTORNO JIM.COM"},
    ]
    assert invoices.open_amount(txns, date(2026, 9, 23), date(2026, 10, 23)) == 1131.67


def test_credito_igual_ao_total_da_fatura_fechada_e_pagamento():
    txns = [{"id": "p", "amount": -415.91, "date": "2026-10-10", "description": "CREDITO DIVERSO"},
            {"id": "c", "amount": 66.90, "date": "2026-10-13", "description": "APPLE.COM/BILL"}]
    assert invoices.open_amount(txns, date(2026, 10, 3), date(2026, 11, 3), {415.91}) == 66.90


def test_pag_boleto_no_cartao_vira_interno_no_sync():
    batch = [("black", CARD, {"id": "p", "amount": -5952.39, "date": "2026-10-05", "description": "PAG BOLETO BANCARIO"})]
    assert "p" in detect_internal(batch)
    # na conta corrente "PAG BOLETO" é conta paga de verdade (luz, condomínio): não vira interno sozinho
    conta = [("cc", {"is_credit_card": False}, {"id": "b", "amount": -180.0, "date": "2026-10-05",
                                                 "description": "PAG BOLETO BANCARIO"})]
    assert "b" not in detect_internal(conta)


# ------------------------------------------------------------- regras de entrada
def test_pix_recebido_pequeno_e_venda_de_carta_e_grande_fica_para_decidir():
    rule = {"match": "pix recebido", "_match": "pix recebido", "category": "Venda de cartas", "tipo": "entrada", "max": 1000}
    assert rule_matches(rule, "PIX RECEBIDO - REM FULANO", 150.0, expense=False)
    assert not rule_matches(rule, "PIX RECEBIDO - REM FULANO", 3500.0, expense=False)
    assert not rule_matches(rule, "PIX RECEBIDO - REM FULANO", 150.0, expense=True)
    sem_tipo = {"match": "copag", "_match": "copag", "category": "Pokémon TCG"}
    assert rule_matches(sem_tipo, "ESTORNO COPAG", 50.0, expense=False)


# ------------------------------------------------------------- meta de poupança
def test_sobra_dos_meses_fechados_ignora_interno_e_mes_sem_dados():
    splits = month("2026-07", 6000, 5000) + month("2026-09", 6000, 4000)
    splits += [sp("2026-09-20", 9999, "withdrawal", "Transferência interna")]
    splits += [sp("2026-08-03", 100.0)]          # agosto com 1 lançamento: sem dados, fica fora
    splits += month("2026-10", 6000, 100)        # mês corrente não entra
    flows = savings.month_flows(splits, TODAY)
    assert [(f["mes"], f["sobra"]) for f in flows] == [("2026-07", 1000.0), ("2026-09", 2000.0)]


def test_meta_e_metade_da_sobra_tipica_com_piso():
    flows = [{"sobra": 1000.0}, {"sobra": 2000.0}, {"sobra": 300.0}]
    p = savings.plan(flows, floor=175.89, rate=0.5, folga_30d=5000.0, today=TODAY)
    assert p["meta_mes"] == 500.0 and p["sobra_tipica"] == 1000.0 and not p["limitada_pela_folga"]
    assert p["guardar_hoje"] == round(500 / 31, 2)
    baixo = savings.plan([{"sobra": 100.0}], floor=175.89, rate=0.5, folga_30d=5000.0, today=TODAY)
    assert baixo["meta_mes"] == 175.89 and "mínimo" in baixo["motivo"]


def test_meta_encolhe_quando_as_contas_dos_30_dias_apertam():
    p = savings.plan([{"sobra": 2000.0}], floor=175.89, rate=0.5, folga_30d=200.0, today=TODAY)
    assert p["meta_mes"] == 100.0 and p["limitada_pela_folga"] and p["meta_cheia"] == 1000.0
    zero = savings.plan([{"sobra": 2000.0}], floor=175.89, rate=0.5, folga_30d=-300.0, today=TODAY)
    assert zero["meta_mes"] == 0.0 and zero["guardar_hoje"] == 0.0


def test_sem_historico_vale_o_piso():
    p = savings.plan([], floor=175.89, rate=0.5, folga_30d=None, today=TODAY)
    assert p["meta_mes"] == 175.89 and p["sobra_tipica"] is None


def test_folga_e_o_ponto_mais_apertado():
    events = [{"date": "2026-10-25", "amount": 3000.0, "direction": "expense"},
              {"date": "2026-11-05", "amount": 6000.0, "direction": "income"},
              {"date": "2026-11-10", "amount": 1000.0, "direction": "expense"}]
    assert tightest_margin(4000.0, events, TODAY, 0.0, 30) == 1000.0


# ------------------------------------------------------------- BCB parcial
def test_bcb_com_502_na_selic_e_no_ipca12_usa_cdi_e_compoe_o_ipca(monkeypatch):
    def fake_sgs(codigo, n=1):
        if codigo in (price_signals.SELIC_META_CODE, price_signals.IPCA_12M_CODE):
            raise RuntimeError("502")
        if codigo == price_signals.CDI_CODE:
            return [{"data": "20/10/2026", "valor": 13.65}]
        return [{"data": f"01/{m:02d}/2026", "valor": 0.5} for m in range(1, n + 1)]
    monkeypatch.setattr(price_signals, "_sgs", fake_sgs)
    out = price_signals.fetch_inflation_and_selic()
    assert out["juro_rotulo"] == "CDI" and out["selic_pct"] == 13.65
    assert out["ipca_12m_pct"] == round((1.005 ** 12 - 1) * 100, 2)
