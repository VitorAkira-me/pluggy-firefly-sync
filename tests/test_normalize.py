"""Limpeza do lote: internas e parcelas (casos reais do export de set/2026)."""
from app.normalize import detect_internal, installment_number, shift_installments

CHK = ("chk", {"is_credit_card": False})
CARD = ("card", {"is_credit_card": True})


def t(id_, amount, date, desc, **extra):
    return {"id": id_, "amount": amount, "date": date, "description": desc, **extra}


def batch(*items):
    return [(acc, tgt, txn) for (acc, tgt), txn in items]


def test_pagamento_de_fatura_e_pix_no_credito_sao_internos():
    b = batch(
        (CHK, t("1", -851.21, "2026-09-21", "Pagamento de fatura FATURA PAGA Itaú Click")),
        (CARD, t("2", -851.21, "2026-09-21", "PAGAMENTO COM SALDO")),
        (CHK, t("3", 147.06, "2026-09-24", "Crédito liberado para Pix Itaú Click M 0809")),
        (CHK, t("4", -147.06, "2026-09-23", "Pix enviado com cartão COPAG.COM - COMERCIO D")),
        (CARD, t("5", 154.14, "2026-09-24", "PIX Copag.Com - Comercio De Cartas Para")),
    )
    found = detect_internal(b)
    assert set(found) == {"1", "2", "3", "4"}  # a compra de verdade (5) continua sendo gasto


def test_pareamento_acha_pagamento_por_valor_mesmo_sem_texto():
    b = batch(
        (CHK, t("a", -178.50, "2026-09-16", "Pagamento de Pix QR Code MERCADO PAGO INSTITU")),
        (CARD, t("b", -178.50, "2026-09-16", "Pagamento recebido")),
    )
    assert set(detect_internal(b)) == {"a", "b"}


def test_valor_igual_na_mesma_conta_nao_vira_interno():
    b = batch((CHK, t("x", -50, "2026-09-10", "LOJA A")), (CHK, t("y", 50, "2026-09-11", "PIX RECEBIDO FULANO")))
    assert detect_internal(b) == {}


def test_parcelas_todas_na_mesma_data_sao_espalhadas():
    b = batch(*[(CARD, t(f"m{n}", 190.0, "2026-09-26", f"MISPLEI CARDSSAO J{n:02d}/10")) for n in range(1, 11)])
    out = shift_installments(b)
    assert out["m1"]["date"] == "2026-09-26"
    assert out["m2"]["date"] == "2026-10-26"
    assert out["m10"]["date"] == "2027-06-26"


def test_parcela_isolada_fica_na_data_e_data_no_texto_nao_e_parcela():
    b = batch((CARD, t("g", 112.5, "2026-09-22", "PG *GDLP COMERCIO 02/08")))
    assert shift_installments(b)["g"]["date"] == "2026-09-22"
    assert installment_number(t("p", 10, "2026-09-16", "PIX RECEBIDO VITOR A16/09"), True) is None
    assert installment_number(t("p", 10, "2026-09-16", "LOJA 01/03"), False) is None  # só cartão


def test_metadata_da_pluggy_tem_prioridade():
    txn = t("z", 100, "2026-09-26", "LOJA", creditCardMetadata={"installmentNumber": 3, "totalInstallments": 5,
                                                                   "purchaseDate": "2026-08-10T00:00:00Z"})
    assert shift_installments(batch((CARD, txn)))["z"]["date"] == "2026-10-10"
