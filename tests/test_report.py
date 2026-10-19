from datetime import date

from app.clients.firefly import next_occurrence
from app.report import compute_safe_daily_spend, fmt_brl


def test_next_occurrence_vira_o_mes_e_respeita_fim_de_mes():
    assert next_occurrence(25, date(2026, 10, 19)) == date(2026, 10, 25)
    assert next_occurrence(4, date(2026, 10, 19)) == date(2026, 11, 4)
    assert next_occurrence(31, date(2026, 11, 5)) == date(2026, 11, 30)
    assert next_occurrence(15, date(2026, 12, 20)) == date(2027, 1, 15)


def test_pode_gastar_considera_salario_antes_da_conta():
    today = date(2026, 10, 10)
    events = [
        {"date": "2026-10-15", "amount": 2400, "direction": "income"},
        {"date": "2026-10-20", "amount": 3000, "direction": "expense"},
    ]
    # até dia 20 (11 dias): (1000 + 2400 - 3000) / 11 = 36,36 ; fim da janela (30 dias): 400/30 = 13,33
    assert round(compute_safe_daily_spend(1000, events, today, 0, 30), 2) == 13.33


def test_pode_gastar_nunca_negativo():
    events = [{"date": "2026-10-12", "amount": 5000, "direction": "expense"}]
    assert compute_safe_daily_spend(100, events, date(2026, 10, 10), 0, 30) == 0.0


def test_fmt_brl():
    assert fmt_brl(1234.5) == "R$ 1.234,50"
