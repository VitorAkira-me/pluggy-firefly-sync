from app.ml import NaiveBayesCategorizer, merchant_key, normalize


def test_normalize_tira_acento_digito_e_ruido():
    assert normalize("COMPRA CARTÃO 1234 Padaria São João*") == "padaria sao joao"


def test_merchant_key_estavel():
    assert merchant_key("UBER *TRIP 8812") == merchant_key("Uber Trip 1999") == "uber trip"


def test_naive_bayes_aprende_padroes():
    descs = ["UBER TRIP", "UBER *TRIP SP", "99 POP CORRIDA", "UBER TRIP HELP"] + ["IFOOD PIZZA", "IFD*IFOOD", "IFOOD LANCHE", "IFOOD JAPA"]
    labels = ["Transporte"] * 4 + ["Alimentação"] * 4
    m = NaiveBayesCategorizer().fit(descs, labels)
    cat, prob = m.predict("UBER *TRIP 9921")
    assert cat == "Transporte" and prob > 0.9


def test_classe_com_poucos_exemplos_e_ignorada():
    m = NaiveBayesCategorizer(min_examples_per_class=3).fit(["A", "B"], ["X", "Y"])
    assert not m.trained and m.predict("A") == (None, 0.0)
