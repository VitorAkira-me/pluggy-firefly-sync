"""Relatório com o dado de ontem (D-1): espera a Pluggy, cache por versão do dado e data de Brasília."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

from app import clock, freshness, invoices, state
from app.scheduler import due_jobs

NOW = datetime(2026, 10, 23, 8, 10, tzinfo=clock.BRT)


def conn(banco, iso):
    return {"banco": banco, "contas": [banco], "atualizado": freshness._ts(iso), "proxima": None, "status": "UPDATED"}


def test_banco_que_nao_atualizou_hoje_esta_atrasado():
    conns = [conn("Nubank", "2026-10-23T09:05:00.000Z"),       # 06:05 de hoje em Brasília
             conn("Itaú", "2026-10-22T23:30:00.000Z")]         # 20:30 de ontem em Brasília
    assert [c["banco"] for c in freshness.stale(conns, NOW)] == ["Itaú"]
    assert "Itaú (22/10 20:30)" in freshness.line(conns, NOW)


def test_todos_atualizados_hoje_valores_ate_ontem():
    conns = [conn("Nubank", "2026-10-23T09:05:00.000Z"), conn("Itaú", "2026-10-23T10:00:00.000Z")]
    assert freshness.stale(conns, NOW) == []
    assert "valores até ontem" in freshness.line(conns, NOW)


def test_utc_depois_das_21h_ainda_e_hoje_em_brasilia():
    late_night = datetime(2026, 10, 24, 1, 30, tzinfo=UTC)   # 22:30 do dia 23 em Brasília
    assert late_night.astimezone(clock.BRT).date() == date(2026, 10, 23)


def test_report_tenta_de_hora_em_hora_ate_sair():
    cfg = SimpleNamespace(sync_every_hours=3, report_hour_utc=8, report_deadline_hour_utc=13,
                          insights_weekday_utc=6, insights_hour_utc=12)
    last, sent = {}, set()
    first = due_jobs(datetime(2026, 10, 23, 8, 0, tzinfo=UTC), cfg, last, lambda j: j in sent)
    assert "app.report" in first and "app.advisor" in first
    again = due_jobs(datetime(2026, 10, 23, 9, 0, tzinfo=UTC), cfg, last, lambda j: j in sent)
    assert "app.report" in again                              # ainda não saiu: tenta às 06:00
    sent |= {"app.report", "app.advisor"}
    assert "app.report" not in due_jobs(datetime(2026, 10, 23, 10, 0, tzinfo=UTC), cfg, last, lambda j: j in sent)
    assert "app.report" not in due_jobs(datetime(2026, 10, 23, 14, 0, tzinfo=UTC), cfg, {}, lambda j: False)


def test_cache_das_faturas_refaz_quando_a_pluggy_atualiza(monkeypatch):
    calls = []
    monkeypatch.setattr(invoices, "estimate", lambda p, pid, m, today: calls.append(pid) or {"amount": 1.0})
    mapping = {"c1": {"firefly_account_name": "Nubank", "is_credit_card": True}}
    day = date(2026, 10, 23)
    state.put("invoice_estimates", {})
    invoices.estimates_cached(None, mapping, day, "v1")
    invoices.estimates_cached(None, mapping, day, "v1")
    invoices.estimates_cached(None, mapping, day, "v2")
    assert calls == ["c1", "c1"]
