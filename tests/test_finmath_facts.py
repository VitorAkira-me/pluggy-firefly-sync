from datetime import date

from app import facts as F
from app import finmath


def test_taxas_equivalentes():
    assert round(finmath.monthly_to_annual(1), 2) == 12.68
    assert round(finmath.annual_to_monthly(15), 4) == 1.1715


def test_custo_efetivo_do_pix_no_credito_real():
    assert finmath.effective_monthly_cost(121.91, [67.70, 67.70]) == 7.29
    assert finmath.effective_monthly_cost(147.06, [154.14]) == 4.81


def test_a_vista_vs_parcelado():
    r = finmath.cash_vs_installments(1000, 100, 10, 14.0)
    assert r["melhor"] == "parcelado" and r["vp_parcelado"] < 1000
    r = finmath.cash_vs_installments(900, 100, 10, 14.0)
    assert r["melhor"] == "à vista"


def test_meses_para_meta():
    assert finmath.months_to_goal(10000, 300, 0) == 34
    assert finmath.months_to_goal(10000, 300, 0.9) == 30
    assert finmath.months_to_goal(100, 0, 0) is None


def sp(d, amt, desc, cat=None, typ="withdrawal", tags=None, src="Itaú Click"):
    return {"date": d, "amount": str(amt), "description": desc, "category_name": cat, "type": typ,
            "tags": tags or [], "source_name": src}


SPLITS = [
    sp("2026-09-23", 121.91, "Pix enviado com cartão COPAG.COM", "Transferência interna", tags=["interno"], src="Itau Conta"),
    sp("2026-09-24", 67.70, "PIX Copag.Com - Co01/02", "Pokémon TCG"),
    sp("2026-10-24", 67.70, "PIX Copag.Com - Co02/02", "Pokémon TCG", tags=["parcela"]),
    sp("2026-09-20", 849.97, "Ri Happy Online", "Pokémon TCG"),
    sp("2026-09-21", 851.21, "Pagamento de fatura", "Transferência interna", tags=["interno"], src="Itau Conta"),
    sp("2026-09-15", 71.04, "MISTER DONUTS", "Alimentação"),
]


def test_internas_ficam_fora_do_gasto():
    cats = F.by_category(SPLITS, date(2026, 9, 1), date(2026, 9, 30))
    assert "Transferência interna" not in cats and cats["Pokémon TCG"] == 917.67


def test_pix_no_credito_casa_com_parcelas():
    r = F.pix_credit_costs(SPLITS, days=60, today=date(2026, 9, 30))
    assert r == [{"data": "2026-09-23", "enviado": 121.91, "cobrado": 135.4, "parcelas": 2, "taxa_paga": 13.49,
                  "custo_mes_pct": 7.29}]


def test_pokemon_e_parcelas_futuras():
    p = F.pokemon_month(SPLITS, date(2026, 9, 30), 500)
    assert p["gasto_mes"] == 917.67 and p["parcelas_futuras"] == 67.70 and p["uso_orcamento_pct"] == 183.5
    assert F.installments_ahead(SPLITS, date(2026, 9, 30)) == [{"mes": "2026-10", "total": 67.70}]
