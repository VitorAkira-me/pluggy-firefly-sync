from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.insights import build_facts, fallback_text, outliers, probable_subscriptions
from app.scheduler import due_jobs

TODAY = date(2026, 10, 19)


def s(d, amt, desc, cat=None):
    return {"date": d, "amount": str(amt), "description": desc, "category_name": cat}


def test_detecta_assinatura_recorrente():
    splits = [s(f"2026-{m:02d}-27", 19.90, "AMAZON PRIME BR", "Assinaturas") for m in (7, 8, 9, 10)]
    subs = probable_subscriptions(splits, TODAY)
    assert subs and subs[0]["estabelecimento"] == "amazon prime" and subs[0]["meses"] >= 3


def test_outlier_pela_regra_de_tukey():
    base = [s(f"2026-09-{d:02d}", 40 + d % 5, "IFOOD", "Alimentação") for d in range(1, 20)]
    splits = base + [s("2026-10-18", 400, "IFOOD FESTA", "Alimentação")]
    out = outliers(splits, TODAY)
    assert out and out[0]["valor"] == 400


def test_fallback_sem_ia_gera_texto():
    splits = [s("2026-10-18", 50, "IFOOD", "Alimentação"), s("2026-09-18", 30, "IFOOD", "Alimentação")]
    assert fallback_text(build_facts(splits, TODAY)).startswith("•")


def test_scheduler_sync_antes_do_report_e_sem_repetir():
    cfg = SimpleNamespace(sync_every_hours=3, report_hour_utc=9, insights_weekday_utc=6, insights_hour_utc=12)
    last = {}
    now = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)  # segunda, 9h UTC
    assert due_jobs(now, cfg, last) == ["app.sync", "app.report", "app.advisor"]
    assert due_jobs(now, cfg, last) == []
