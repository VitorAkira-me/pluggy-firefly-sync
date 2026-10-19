from datetime import date

from app import advisor, lessons, state


def test_checagem_de_numeros_descarta_invencao():
    allowed = advisor.allowed_numbers({"gasto": 2113.08, "pct": 422.6})
    assert advisor.grounded("Você gastou R$ 2.113,08 com Pokémon, 422,6% do orçamento.", allowed)
    assert not advisor.grounded("Se cortar, economiza R$ 1.500 por mês.", allowed)
    assert advisor.grounded("Faça 1 coisa hoje.", allowed)


def test_aula_escolhida_pelo_gatilho_e_sem_repetir():
    all_ = lessons.load_all()
    assert len(all_) >= 15 and all(lesson.question() for lesson in all_)
    pix = lessons.pick({"pix_credito"}, all_, taught=set())
    assert pix.slug == "05-pix-no-credito"
    other = lessons.pick({"pix_credito"}, all_, taught={"05-pix-no-credito"})
    assert other.slug != "05-pix-no-credito"


def test_gestor_por_regras_sem_ia():
    f = {
        "pode_gastar_hoje": 0.0, "mes": {"total": 4389.56, "categorias": {"Pokémon TCG": 2113.08}},
        "pokemon": {"gasto_mes": 2113.08, "orcamento": 500.0, "uso_orcamento_pct": 422.6, "parcelas_futuras": 1777.7},
        "pix_credito": [{"taxa_paga": 25.70}], "parcelas_futuras": [{"mes": "2026-11", "total": 190.0}],
        "faturas_desatualizadas": ["Bradesco Black"], "a_revisar": 3, "faturas_vencendo_5_dias": [],
        "mercado": {"ciclo_selic": "estável"},
    }
    lesson = lessons.pick(advisor.triggers(f), taught=set())
    r, origin = advisor.compose(f, lesson, None)
    text = advisor.format_message(r, lesson, origin, date(2026, 10, 20))
    assert origin == "regras" and "/fatura" in r["acao_do_dia"] and "422%" in text.replace("423%", "422%")
    assert "Aula" in text and "❓" in text


def test_estado_faturas_e_parte_do_black():
    state.set_manual_invoice("Bradesco Black", 2450, my_share=True)
    inv = state.manual_invoices()["Bradesco Black"]
    assert inv["amount"] == 2450.0 and inv["minha_parte"] is True
    with state.llm_lock(timeout=0) as a:
        with state.llm_lock(timeout=0) as b:
            assert a and not b


class FakeLLM:
    def chat_json(self, system, user, schema, temperature=0.0, priority="lote"):
        return {"manchete": "Pokémon passou do orçamento em 422,6%.",
                "observacoes": ["Você gastou R$ 2.113,08 com Pokémon.", "Cortando tudo você guardaria R$ 9.999 no ano."],
                "acao_do_dia": "Defina um teto para Pokémon.", "aula": "Juros compostos crescem no expoente.",
                "pergunta": "Qual compra você refaria?"}


def test_gestor_com_ia_descarta_frase_com_numero_inventado():
    f = {"pode_gastar_hoje": 0.0, "mes": {"total": 4389.56, "categorias": {"Pokémon TCG": 2113.08}},
         "pokemon": {"gasto_mes": 2113.08, "orcamento": 500.0, "uso_orcamento_pct": 422.6, "parcelas_futuras": 0},
         "pix_credito": [], "parcelas_futuras": [], "faturas_desatualizadas": [], "a_revisar": 0,
         "faturas_vencendo_5_dias": [], "mercado": {}}
    lesson = lessons.pick({"sempre"}, taught=set())
    r, origin = advisor.compose(f, lesson, FakeLLM())
    assert origin == "IA local"
    assert r["observacoes"] == ["Você gastou R$ 2.113,08 com Pokémon."]
    assert r["manchete"].startswith("Pokémon passou")
