from app.classify import Classifier


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def chat_json(self, system, user, schema):
        self.calls += 1
        return self.answer


HIST = [
    {"description": "PADARIA SANTANA LTDA", "category_name": "Alimentação"},
    {"description": "PADARIA SANTANA LTDA", "category_name": "Alimentação"},
    {"description": "POSTO SHELL ARUJA", "category_name": "Transporte"},
]
CATS = ["Alimentação", "Transporte", "Assinaturas", "Lazer"]


def test_regra_vence_e_llm_nem_e_chamado():
    llm = FakeLLM({"category": "Lazer", "merchant": "x", "confidence": 0.99, "reason": "x"})
    d = Classifier(HIST, CATS, llm).classify("IFOOD *PIZZA", 50)
    assert (d.category, d.stage) == ("Alimentação", "regra") and llm.calls == 0


def test_historico_antes_do_llm():
    llm = FakeLLM({"category": "Lazer", "merchant": "x", "confidence": 0.99, "reason": "x"})
    d = Classifier(HIST, CATS, llm).classify("PADARIA SANTANA 0042", 12)
    assert (d.category, d.stage) == ("Alimentação", "historico") and llm.calls == 0


def test_llm_aplica_com_confianca_alta():
    llm = FakeLLM({"category": "Lazer", "merchant": "Cinemark", "confidence": 0.92, "reason": "cinema"})
    d = Classifier(HIST, CATS, llm).classify("CINEMARK SHOPPING", 60)
    assert (d.category, d.stage) == ("Lazer", "llm")


def test_llm_confianca_baixa_vai_para_revisao():
    llm = FakeLLM({"category": "Lazer", "merchant": "?", "confidence": 0.55, "reason": "talvez"})
    d = Classifier(HIST, CATS, llm).classify("XPTO 123", 60)
    assert d.category is None and d.needs_review and "Lazer" in d.reason


def test_llm_categoria_inventada_e_descartada():
    llm = FakeLLM({"category": "Criptomoedas", "merchant": "?", "confidence": 0.99, "reason": "x"})
    d = Classifier(HIST, CATS, llm).classify("XPTO 123", 60)
    assert d.needs_review


def test_sem_llm_cai_em_revisao():
    d = Classifier(HIST, CATS, None).classify("LOJA DESCONHECIDA", 10)
    assert d.needs_review and d.stage == "revisar"


def test_entrada_so_usa_regra_e_nao_pede_revisao():
    llm = FakeLLM({"category": "Lazer", "merchant": "x", "confidence": 0.99, "reason": "x"})
    d = Classifier(HIST, CATS, llm).classify("PIX RECEBIDO FULANO", 100, expense=False)
    assert d.category is None and not d.needs_review and llm.calls == 0
