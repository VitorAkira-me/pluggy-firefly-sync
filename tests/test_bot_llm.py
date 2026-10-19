from datetime import UTC, datetime

from app import bot
from app.clients.llm import in_window


def test_parse_brl():
    assert bot.parse_brl("1.234,56") == 1234.56
    assert bot.parse_brl("35,9") == 35.9
    assert bot.parse_brl("R$ 40") == 40.0
    assert bot.parse_brl("abc") is None


def test_match_card_e_texto_livre():
    names = ["Bradesco Black", "Bradesco Casas Bahia", "Itaú Click", "Nubank", "Mercado Pago"]
    assert bot.match_card("black", names) == "Bradesco Black"
    assert bot.match_card("itau", names) == "Itaú Click"
    assert bot.match_card("bradesco", names) is None  # ambíguo: pede de novo
    assert bot.parse_free_expense("gastei 40 no ifood") == (40.0, "ifood")
    assert bot.parse_free_expense("paguei R$ 12,50 de estacionamento") == (12.5, "estacionamento")
    assert bot.parse_free_expense("quanto gastei?") is None


def test_janela_da_gpu_espelha_agenda_do_radar():
    # 05:00 BRT de segunda = 08:00 UTC: dentro (madrugada); 10:00 BRT de segunda: fora; 10:00 BRT de terça: dentro
    assert in_window(datetime(2026, 10, 19, 8, 0, tzinfo=UTC))
    assert not in_window(datetime(2026, 10, 19, 13, 0, tzinfo=UTC))
    assert in_window(datetime(2026, 10, 20, 13, 0, tzinfo=UTC))
