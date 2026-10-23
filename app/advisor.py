"""Gestor financeiro diário: organiza (fatos e ação do dia) e ensina (aula conectada aos seus dados).

Fluxo da manhã (depois do resumo numérico do app.report):
1. Python monta os FATOS: resumo do dia, mês por categoria, parcelas futuras, custo do
   Pix no crédito, Pokémon vs orçamento, metas, mercado (Selic/CDI/IPCA e ciclo),
   pendências (faturas desatualizadas, transações a revisar) e o que o gestor disse
   nos últimos dias (para não se repetir e para cobrar o que sugeriu).
2. Escolhe a aula do dia (app/lessons.py) pelos gatilhos dos fatos.
3. O LLM local recebe fatos + aula e devolve JSON (manchete, observações, ação, aula
   adaptada, pergunta). Toda frase com número que não está nos fatos é descartada
   (checagem anti-alucinação). Sem IA, sai a versão por regras com os mesmos fatos.

Uso: `python -m app.advisor [--no-send]`.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, timedelta

from app import clock, freshness, lessons, market, state
from app import facts as F
from app.clients.firefly import FireflyClient
from app.clients.llm import LocalLLM
from app.clients.pluggy import PluggyClient
from app.clients.telegram import send_message
from app.config import load_account_mapping, load_settings
from app.finmath import annual_to_monthly
from app.report import compute_report, fmt_brl

log = logging.getLogger("advisor")

SCHEMA = {
    "type": "object",
    "properties": {
        "manchete": {"type": "string"},
        "observacoes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
        "acao_do_dia": {"type": "string"},
        "aula": {"type": "string"},
        "pergunta": {"type": "string"},
    },
    "required": ["manchete", "observacoes", "acao_do_dia", "aula", "pergunta"],
}

SYSTEM = (
    "Você é o gestor financeiro pessoal do Akira, analista de dados de 30 e poucos anos que gosta de aprender "
    "e de entender o porquê das coisas. Seu trabalho tem duas partes: organizar (dizer o que importa hoje e uma "
    "ação concreta) e ensinar (explicar o conceito da aula usando os números dele). "
    "Regras: português do Brasil, tom natural e direto, sem travessão, sem jargão sem explicar. "
    "Use SOMENTE números que aparecem nos FATOS ou no texto da AULA; nunca calcule nada novo. "
    "Não recomende produto financeiro específico nem diga para comprar ou vender ativo. "
    "Pokémon TCG é CONSUMO (hobby, gasto de desejo), não investimento nem negócio: trate o teto mensal do "
    "orçamento como limite de lazer e nunca justifique compra por possível revenda; vendas de cartas "
    "(pokemon.vendas_mes) só reduzem o líquido, não liberam mais gasto. "
    "meta_poupanca é calculada pelas contas dele (motivo explica o porquê); se limitada_pela_folga for true, "
    "explique que este mês o aperto vem das faturas e contas, sem culpa. "
    "Juros, multa ou encargo (categoria Taxas e juros) é dinheiro perdido: aponte e diga como evitar. "
    "Se algo do que você sugeriu nos últimos dias (campo historico) continua pendente, cobre com gentileza. "
    "manchete: 1 frase. observacoes: 2 a 4 frases curtas, cada uma com um fato e o que ele significa. "
    "acao_do_dia: 1 ação que dá para fazer hoje em até 10 minutos. aula: 3 a 5 frases adaptando a AULA aos fatos. "
    "pergunta: 1 pergunta socrática para ele pensar, sem dar a resposta."
)


# --------------------------------------------------------------------- fatos
def gather(today: date | None = None) -> dict:
    s = load_settings()
    today = today or clock.today()
    ff = FireflyClient(s.firefly_base_url, s.firefly_token)
    pluggy = PluggyClient(s.pluggy_client_id, s.pluggy_client_secret)
    splits = list(ff.iter_splits(today - timedelta(days=180), today + timedelta(days=365), "all"))
    mapping = load_account_mapping()
    conns = freshness.connections(pluggy, mapping)
    report = compute_report(pluggy, ff, mapping, s.monthly_savings_target, s.safety_buffer, today,
                            savings_rate=s.savings_rate, data_version=freshness.version(conns))
    report["bancos_atrasados"] = [c["banco"] for c in freshness.stale(conns)]
    try:
        mk = market.snapshot()
    except Exception as exc:  # noqa: BLE001
        log.warning("mercado indisponível: %s", exc)
        mk = {}
    budgets = {b["name"]: b["monthly"] for b in ff.list_budgets()}
    rate = annual_to_monthly(mk.get("cdi_aa", 0) or 0)
    meta = (report.get("savings_plan") or {}).get("meta_mes", s.monthly_savings_target)
    return build_facts(splits, report, mk, budgets, ff.list_piggy_banks(), meta, rate, today)


def build_facts(splits, report, mk, budgets, piggies, monthly_saving, rate_am, today) -> dict:
    invoices = state.manual_invoices()
    # só sobra trabalho manual quando a fatura não pôde ser calculada (sem faturas fechadas na Pluggy)
    stale = [c["name"] for c in report.get("card_items", []) if c.get("source") in ("pluggy", "mapeamento")]
    due_soon = [c for c in report.get("card_items", []) if c.get("due_date")
                and 0 <= (date.fromisoformat(c["due_date"]) - today).days <= 5]
    review = sum(1 for x in splits if "revisar" in (x.get("tags") or []))
    return {
        "hoje": today.isoformat(),
        "pode_gastar_hoje": report.get("free_to_spend_today"),
        "guardar_hoje": report.get("save_today"),
        "saldo": report.get("balance"),
        "faturas_30d": report.get("cards_due_total"),
        "faturas_vencendo_5_dias": [{"cartao": c["name"], "valor": c["amount"], "vence": c["due_date"]} for c in due_soon],
        "faturas_desatualizadas": stale,
        "bancos_sem_atualizar_hoje": report.get("bancos_atrasados", []),
        "mes": F.month_summary(splits, today),
        "parcelas_futuras": F.installments_ahead(splits, today),
        "pix_credito": F.pix_credit_costs(splits, today=today),
        "pokemon": F.pokemon_month(splits, today, budgets.get(F.POKEMON)),
        "maiores_estabelecimentos_30d": F.top_merchants(splits, today - timedelta(days=29), today),
        "metas": F.goals_progress(piggies, monthly_saving, rate_am),
        "meta_poupanca": {k: v for k, v in (report.get("savings_plan") or {}).items() if k != "meses"},
        "mercado": mk,
        "a_revisar": review,
        "valores_informados_pelo_bot": invoices,
        "historico": [{"em": e["at"][:10], "acao": e.get("acao"), "manchete": e.get("manchete")}
                      for e in state.recent_events("insight", 3)],
    }


def triggers(f: dict) -> set[str]:
    t = {"sempre"}
    if f["pix_credito"]:
        t.add("pix_credito")
    if f["parcelas_futuras"]:
        t.add("parcela")
    if not f.get("pode_gastar_hoje"):
        t.add("saldo_baixo")
    p = f["pokemon"]
    if p.get("uso_orcamento_pct") and p["uso_orcamento_pct"] > 80:
        t |= {"pokemon", "impulso"}
    if f["faturas_vencendo_5_dias"]:
        t |= {"fatura", "cartao"}
    if f["mercado"].get("ciclo_selic") in ("alta", "queda"):
        t.add("mercado")
    return t


# ------------------------------------------------------ checagem de números
_NUM = re.compile(r"\d[\d.]*,\d+|\d+(?:\.\d+)?")


def _norm(token: str) -> set[str]:
    """'1.234,50' e '1234.5' viram o mesmo conjunto de formas, para comparar."""
    t = token.strip(".,")
    if "," in t or re.fullmatch(r"\d{1,3}(\.\d{3})+", t):  # 1.234,56 ou 9.999 (milhar com ponto)
        t = t.replace(".", "").replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return {token}
    return {f"{v:.2f}", f"{v:.1f}", f"{round(v):d}"}


def allowed_numbers(*sources) -> set[str]:
    out: set[str] = set()
    for src in sources:
        text = json.dumps(src, ensure_ascii=False) if not isinstance(src, str) else src
        for tok in _NUM.findall(text):
            out |= _norm(tok)
    return out


def grounded(sentence: str, allowed: set[str]) -> bool:
    for tok in _NUM.findall(sentence):
        if len(tok.strip(".,")) <= 1:  # "1 ação", "3 dias": números pequenos de texto passam
            continue
        if not (_norm(tok) & allowed):
            return False
    return True


# ------------------------------------------------------------------ texto
def rule_based(f: dict, lesson: lessons.Lesson) -> dict:
    obs = []
    m = f["mes"]
    if m["total"]:
        top = next(iter(m["categorias"].items()), None)
        if top:
            obs.append(f"No mês você gastou {fmt_brl(m['total'])}; a maior fatia foi {top[0]} ({fmt_brl(top[1])}).")
    p = f["pokemon"]
    if p.get("orcamento") and p.get("uso_orcamento_pct") is not None:
        obs.append(f"Pokémon TCG: {fmt_brl(p['gasto_mes'])} no mês, {p['uso_orcamento_pct']:.0f}% do orçamento de "
                   f"{fmt_brl(p['orcamento'])}.")
    if f.get("bancos_sem_atualizar_hoje"):
        obs.append("Ainda sem atualizar hoje na Pluggy: " + ", ".join(f["bancos_sem_atualizar_hoje"])
                   + "; os números desses bancos estão atrasados.")
    meta = f.get("meta_poupanca") or {}
    if meta.get("meta_mes") is not None:
        obs.append(f"Meta de guardar no mês: {fmt_brl(meta['meta_mes'])} ({meta['motivo']}).")
    fees = m["categorias"].get("Taxas e juros")
    if fees:
        obs.append(f"Você pagou {fmt_brl(fees)} em juros, multa ou encargo no mês; pagar até o vencimento zera isso.")
    if f["pix_credito"]:
        taxa = sum(x["taxa_paga"] for x in f["pix_credito"])
        obs.append(f"Pix no crédito custou {fmt_brl(taxa)} em taxas nos últimos 60 dias.")
    if f["parcelas_futuras"]:
        prox = f["parcelas_futuras"][0]
        y, m = prox["mes"].split("-")
        obs.append(f"Já há {fmt_brl(prox['total'])} em parcelas para {m}/{y}.")
    if f["faturas_desatualizadas"]:
        acao = (f"Não consegui calcular a fatura de {', '.join(f['faturas_desatualizadas'])}; se souber o valor, "
                "mande /fatura (vale só até ela fechar).")
    elif f["a_revisar"]:
        acao = f"Revise {f['a_revisar']} transações pendentes com /revisar (2 minutos)."
    else:
        acao = "Abra /hoje antes de qualquer compra não planejada."
    return {"manchete": f"Pode gastar hoje: {fmt_brl(f.get('pode_gastar_hoje') or 0)}.",
            "observacoes": obs[:4] or ["Sem novidades relevantes desde ontem."],
            "acao_do_dia": acao, "aula": lesson.text_without_question(), "pergunta": lesson.question()}


def compose(f: dict, lesson: lessons.Lesson, llm: LocalLLM | None, priority: str = "lote") -> tuple[dict, str]:
    base = rule_based(f, lesson)
    if not llm:
        return base, "regras"
    user = (f"FATOS:\n{json.dumps(f, ensure_ascii=False)}\n\nAULA ({lesson.title}):\n{lesson.body}")
    out = llm.chat_json(SYSTEM, user, SCHEMA, temperature=0.3, priority=priority)
    if not out:
        return base, "regras"
    allowed = allowed_numbers(f, lesson.body)
    obs = [o for o in out.get("observacoes", []) if isinstance(o, str) and grounded(o, allowed)]
    result = {
        "manchete": out["manchete"] if grounded(out.get("manchete", ""), allowed) else base["manchete"],
        "observacoes": obs or base["observacoes"],
        "acao_do_dia": out["acao_do_dia"] if grounded(out.get("acao_do_dia", ""), allowed) else base["acao_do_dia"],
        "aula": out["aula"] if grounded(out.get("aula", ""), allowed) else base["aula"],
        "pergunta": out.get("pergunta") or base["pergunta"],
    }
    dropped = len(out.get("observacoes", [])) - len(obs)
    if dropped:
        log.info("gestor: %d observação(ões) descartada(s) por número fora dos fatos", dropped)
    return result, "IA local"


def format_message(r: dict, lesson: lessons.Lesson, origin: str, today: date) -> str:
    lines = [f"🧭 *Gestor, {today.strftime('%d/%m')}*", r["manchete"], ""]
    lines += [f"• {o}" for o in r["observacoes"]]
    lines += ["", f"✅ *Ação de hoje:* {r['acao_do_dia']}", "", f"📚 *Aula: {lesson.title}*", r["aula"]]
    if r.get("pergunta"):
        lines += ["", f"❓ {r['pergunta']}", "_Responda aqui no chat que eu comento._"]
    lines += ["", f"_{origin}; números calculados pelo sistema._"]
    return "\n".join(lines)


def main(send: bool = True, priority: str = "lote") -> str:
    s = load_settings()
    today = clock.today()
    if send and priority == "lote" and state.get("report_sent_day") != today.isoformat():
        print("gestor adiado: o resumo do dia ainda não saiu (esperando a Pluggy atualizar)")
        return ""
    f = gather(today)
    lesson = lessons.pick(triggers(f))
    llm = LocalLLM(s.llm_base_url, s.llm_model, s.llm_timeout) if s.llm_enabled else None
    result, origin = compose(f, lesson, llm, priority)
    text = format_message(result, lesson, origin, today)
    print(text)
    if send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
        lessons.mark_taught(lesson)
        if priority == "lote":
            state.put("advisor_sent_day", today.isoformat())
        state.log_event("insight", {"manchete": result["manchete"], "acao": result["acao_do_dia"], "origem": origin})
        state.put("ultima_aula", {"slug": lesson.slug, "titulo": lesson.title, "pergunta": result.get("pergunta"),
                                  "dia": today.isoformat()})
    return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-send", action="store_true")
    main(send=not p.parse_args().no_send)
