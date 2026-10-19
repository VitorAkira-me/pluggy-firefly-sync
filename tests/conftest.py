import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def fake_config(tmp_path, monkeypatch):
    """Config de teste isolada: nunca usa os ids reais nem a rede."""
    (tmp_path / "account_mapping.json").write_text(json.dumps({"mappings": [
        {"pluggy_account_id": "chk-1", "firefly_account_id": 31, "firefly_account_name": "Itau Conta Corrente", "is_credit_card": False},
        {"pluggy_account_id": "card-1", "firefly_account_id": 5, "firefly_account_name": "Itaú Click", "is_credit_card": True},
    ]}), encoding="utf-8")
    (tmp_path / "category_rules.json").write_text(json.dumps({"rules": [
        {"match": "ifood", "category": "Alimentação"},
        {"match": "claude.ai", "category": "Assinaturas", "expense_account": "Claude"},
    ], "internal": ["pagamento de fatura", "pagamento com saldo", "crédito liberado para pix", "pix enviado com cartão"],
        "categories": ["Alimentação", "Assinaturas", "Lazer", "Transporte"]}), encoding="utf-8")
    for name in ("llm_janelas.json", "price_signals.json"):
        shutil.copy(ROOT / "config" / name, tmp_path / name)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("PF_DATA_DIR", str(data))
    import app.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    for fn in (cfg.load_account_mapping, cfg.load_category_rules, cfg.load_category_rules_file, cfg.load_price_signals_config, cfg.load_llm_windows):
        fn.cache_clear()
    for var in ("LLM_BASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_USER_ID", "ACCOUNT_MAPPING_JSON"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path
    for fn in (cfg.load_account_mapping, cfg.load_category_rules, cfg.load_category_rules_file, cfg.load_price_signals_config, cfg.load_llm_windows):
        fn.cache_clear()
