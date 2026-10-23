"""Bot de mão dupla no Telegram: você recebe e também atualiza.

Comandos (o menu do Telegram mostra os principais):
  /hoje                       resumo agora (pode gastar, faturas, mês)
  /gestor                     análise e aula agora (a mesma da manhã)
  /fatura black 2450          quanto você vai pagar de uma fatura (no Black: a SUA parte)
  /gasto 35,90 almoço #Alimentação   lança um gasto em dinheiro no Firefly
  /revisar                    classifica pendências com botões (e "📌 Sempre" vira regra)
  /simular 1200 120 10        à vista x 10x de 120 com o CDI do dia
  /metas  /parcelas  /pix  /mercado  /aula [tema]  /ajuda
Texto livre: "gastei 40 no ifood" vira um lançamento para confirmar; perguntas ("quanto
gastei com Pokémon este mês?") vão para o gestor (LLM local com ferramentas), se a GPU
estiver liberada.

Segurança: só responde ao chat privado e ao usuário configurados; qualquer outro update é ignorado.
Cada mensagem é tratada numa thread separada (pool de 2), então uma resposta lenta da IA
não trava os botões nem o agendador.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from app import clock, finmath, lessons, market, state
from app import facts as F
from app.clients import telegram as tg
from app.clients.firefly import FireflyClient
from app.clients.llm import LocalLLM
from app.config import load_account_mapping, load_settings

log = logging.getLogger("bot")

MENU = [("hoje", "resumo agora"), ("gestor", "análise e aula"), ("fatura", "informar valor de fatura"),
        ("gasto", "lançar gasto manual"), ("revisar", "classificar pendências"), ("ajuda", "todos os comandos")]

HELP = """*Comandos*
/hoje: resumo agora
/gestor: análise do dia e aula
/fatura black 2450: quanto você vai pagar (no Black, a sua parte)
/gasto 35,90 almoço #Alimentação: gasto em dinheiro
/revisar: classificar pendências com botões
/simular 1200 120 10: à vista x 10x de 120
/regra nome da pessoa #Pokémon TCG: sempre classificar esse texto assim (vale também para Pix)
/metas: meta de guardar do mês (calculada pelas contas) e cofrinhos
/parcelas  /pix  /mercado
/aula [tema]: uma aula agora (ex.: /aula juros)
Ou escreva normalmente: "gastei 40 no ifood", "quanto gastei com Pokémon?\""""


# ------------------------------------------------------------------ parsing
def parse_brl(text: str) -> float | None:
    t = text.strip().replace("R$", "").strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{3})*(,\d{1,2})?|\d+([.,]\d{1,2})?", t):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def match_card(query: str, names: list[str]) -> str | None:
    q = _plain(query)
    hits = [n for n in names if q in _plain(n)]
    return hits[0] if len(hits) == 1 else None


_GASTEI = re.compile(r"^(?:gastei|paguei|comprei)\s+(?:r\$\s*)?([\d.,]+)\s*(?:reais)?\s*(?:no|na|em|de|com)?\s*(.*)$", re.I)


def parse_free_expense(text: str) -> tuple[float, str] | None:
    m = _GASTEI.match(text.strip())
    if not m:
        return None
    value = parse_brl(m.group(1))
    return (value, m.group(2).strip() or "gasto") if value else None


# ----------------------------------------------------------------- handlers
class Bot:
    def __init__(self):
        self.s = load_settings()
        self.ff = FireflyClient(self.s.firefly_base_url, self.s.firefly_token)
        self.llm = LocalLLM(self.s.llm_base_url, self.s.llm_model, self.s.llm_timeout) if self.s.llm_enabled else None
        self.pool = ThreadPoolExecutor(max_workers=2)

    def say(self, text: str, buttons=None) -> dict:
        return tg.send_message(self.s.telegram_bot_token, self.s.telegram_chat_id, text, buttons)

    # -- comandos
    def cmd_hoje(self, _args: str) -> None:
        from app.report import main as report_main
        report_main(send=True, force=True)

    def cmd_gestor(self, _args: str) -> None:
        from app.advisor import main as advisor_main
        advisor_main(send=True, priority="agora")

    def cmd_fatura(self, args: str) -> None:
        parts = args.split()
        value = parse_brl(parts[-1]) if parts else None
        day = None
        if len(parts) >= 3 and parts[-1].isdigit() and int(parts[-1]) <= 31 and parse_brl(parts[-2]):
            day, value = int(parts[-1]), parse_brl(parts[-2])
            parts = parts[:-1]
        names = [m["firefly_account_name"] for m in load_account_mapping().values() if m.get("is_credit_card")]
        card = match_card(" ".join(parts[:-1]), names) if len(parts) >= 2 else None
        if not card or value is None:
            self.say("Use: /fatura <cartão> <valor> [dia]\nEx.: /fatura black 2450 ou /fatura itau 564,85 22\n"
                     f"Cartões: {', '.join(names)}")
            return
        shared = card == "Bradesco Black" or any(m.get("shared") and m["firefly_account_name"] == card
                                                 for m in load_account_mapping().values())
        entry = state.set_manual_invoice(card, value, day, my_share=shared)
        extra = " (a sua parte)" if shared else ""
        self.say(f"✅ {card}: R$ {entry['amount']:.2f}{extra} anotado. Vale até essa fatura fechar; depois o sistema "
                 "volta a calcular sozinho pela Pluggy.")

    def cmd_gasto(self, args: str) -> None:
        m = re.match(r"([\d.,]+)\s+(.+)", args.strip())
        if not m or parse_brl(m.group(1)) is None:
            self.say("Use: /gasto <valor> <descrição> [#Categoria]\nEx.: /gasto 35,90 almoço #Alimentação")
            return
        value, rest = parse_brl(m.group(1)), m.group(2)
        cat = None
        if "#" in rest:
            rest, cat = rest.split("#", 1)
            cat = cat.strip()
        self.create_cash_expense(value, rest.strip(), cat)

    def create_cash_expense(self, value: float, desc: str, category: str | None) -> None:
        src = self.ff.find_account_id("Dinheiro") or self.ff.find_account_id("Carteira")
        if not src:
            self.say("Não achei a conta \"Dinheiro\" no Firefly. Crie uma conta de ativo com esse nome e tente de novo.")
            return
        today = clock.today().isoformat()
        self.ff.create_transaction(type_="withdrawal", date_=today, amount=f"{value:.2f}", description=desc,
                                   external_id=f"manual:{int(time.time() * 1000)}", source_id=src,
                                   destination_name=desc[:40], category_name=category, tags=["manual"],
                                   notes="lançado pelo bot")
        state.log_event("gasto_manual", {"valor": value, "desc": desc, "categoria": category})
        self.say(f"✅ Gasto de R$ {value:.2f} lançado: {desc}" + (f" ({category})" if category else ""))

    def cmd_revisar(self, _args: str) -> None:
        queue = state.get("review_queue", []) or []
        if not queue:
            self.say("Nada para revisar agora. 🎉 (a fila é montada toda madrugada)")
            return
        self.show_review(0, queue)

    def show_review(self, i: int, queue: list[dict], message_id: int | None = None) -> None:
        if i >= len(queue):
            text = "Fila de revisão concluída. ✅"
            if message_id:
                tg.edit_message(self.s.telegram_bot_token, self.s.telegram_chat_id, message_id, text)
            else:
                self.say(text)
            return
        it = queue[i]
        text = f"🔎 *Revisar {i + 1}/{len(queue)}*\n{it['date']}  R$ {it['amount']:.2f}\n`{it['desc']}`\nQual a categoria?"
        rows = [[(c, f"rv|{i}|{k}"), ("📌 Sempre", f"rs|{i}|{k}")] for k, c in enumerate(it["sugestoes"])]
        rows.append([("Pular", f"rx|{i}")])
        if message_id:
            tg.edit_message(self.s.telegram_bot_token, self.s.telegram_chat_id, message_id, text, rows)
        else:
            self.say(text, rows)

    def cmd_simular(self, args: str) -> None:
        nums = [parse_brl(x) for x in args.split()]
        if len(nums) != 3 or None in nums:
            self.say("Use: /simular <preço à vista> <valor da parcela> <nº de parcelas>\nEx.: /simular 1200 120 10")
            return
        cash, inst, n = nums
        cdi = market.snapshot().get("cdi_aa")
        if cdi is None:
            self.say("Não consegui o CDI do Banco Central agora; tente mais tarde.")
            return
        r = finmath.cash_vs_installments(cash, inst, int(n), cdi)
        self.say(
            f"📐 *À vista x parcelado* (CDI {cdi:.2f}% a.a. = {r['taxa_mes_pct']:.2f}% a.m.)\n"
            f"À vista: R$ {r['a_vista']:.2f}\nParcelado: {int(n)}x R$ {inst:.2f} = R$ {r['total_parcelado']:.2f}\n"
            f"Valor hoje das parcelas: R$ {r['vp_parcelado']:.2f}\n"
            f"Melhor: *{r['melhor']}* (diferença hoje R$ {abs(r['diferenca_hoje']):.2f})\n"
            "_Lembre: parcelar ocupa as próximas faturas. Veja /aula parcela._")

    def cmd_regra(self, args: str) -> None:
        if "#" not in args or not args.split("#", 1)[0].strip() or not args.split("#", 1)[1].strip():
            self.say("Use: /regra <texto da descrição> #<Categoria>\nEx.: /regra nome do vendedor #Pokémon TCG\n"
                     "Fica guardada só aqui no servidor (nunca no GitHub) e vale a partir do próximo sync.")
            return
        from app.classify import add_user_rule
        match, cat = (x.strip() for x in args.split("#", 1))
        add_user_rule(match, cat)
        state.log_event("regra_bot", {"texto": match, "categoria": cat})
        self.say(f"📌 Regra salva: \"{match}\" vira *{cat}*. Para corrigir o que já entrou, rode o fix_history "
                 "ou classifique no /revisar.")

    def cmd_metas(self, _args: str) -> None:
        cdi = market.snapshot().get("cdi_aa") or 0
        plan = state.get("savings_plan") or {}
        meta = plan.get("meta_mes", self.s.monthly_savings_target)
        rows = F.goals_progress(self.ff.list_piggy_banks(), meta, finmath.annual_to_monthly(cdi))
        head = [f"🏦 *Guardar este mês: R$ {meta:.2f}*"]
        if plan:
            head.append(f"_{plan.get('motivo', '')}_")
            if plan.get("sobra_tipica") is not None:
                head.append(f"Sobra típica: R$ {plan['sobra_tipica']:.2f} | folga 30 dias: R$ {plan.get('folga_30d') or 0:.2f}"
                            f" | mínimo: R$ {plan.get('piso', 0):.2f}")
        if not rows:
            self.say("\n".join(head + ["", "Nenhum cofrinho no Firefly ainda."]))
            return
        lines = head + ["", "🎯 *Cofrinhos* (a meta dividida igual entre eles)"] + [
            f"• {r['meta']}: R$ {r['atual']:.2f} de R$ {r['alvo']:.2f}"
            + (f", ~{r['meses_no_ritmo']} meses no ritmo atual" if r["meses_no_ritmo"] else "") for r in rows]
        self.say("\n".join(lines))

    def _splits(self, back: int = 180, ahead: int = 365) -> list[dict]:
        today = clock.today()
        return list(self.ff.iter_splits(today - timedelta(days=back), today + timedelta(days=ahead), "all"))

    def cmd_parcelas(self, _args: str) -> None:
        rows = F.installments_ahead(self._splits(30), clock.today(), 12)
        self.say("🧾 *Parcelas a vencer*\n" + ("\n".join(f"• {r['mes']}: R$ {r['total']:.2f}" for r in rows)
                                               or "Nenhuma parcela futura lançada."))

    def cmd_pix(self, _args: str) -> None:
        rows = F.pix_credit_costs(self._splits(90, 400), days=90)
        if not rows:
            self.say("Nenhum Pix no crédito identificado nos últimos 90 dias.")
            return
        total = sum(r["taxa_paga"] for r in rows)
        lines = ["💳 *Pix no crédito (90 dias)*"] + [
            f"• {r['data']}: enviou R$ {r['enviado']:.2f}, pagará R$ {r['cobrado']:.2f} em {r['parcelas']}x "
            f"({r['custo_mes_pct']}% a.m.)" for r in rows] + [f"Total em taxas: *R$ {total:.2f}*. Veja /aula pix."]
        self.say("\n".join(lines))

    def cmd_mercado(self, _args: str) -> None:
        m = market.snapshot()
        if not m:
            self.say("Banco Central indisponível agora.")
            return
        self.say(f"🏦 *Mercado*\nSelic {m.get('selic_aa')}% a.a. (há 6 meses: {m.get('selic_6m_atras')}%, ciclo de "
                 f"{m.get('ciclo_selic')})\nCDI {m.get('cdi_aa')}% a.a.\nIPCA {m.get('ipca_mes_ref', '')}: {m.get('ipca_mes')}% "
                 f"| 12 meses: {m.get('ipca_12m')}%\nJuro real (CDI - IPCA 12m): {m.get('juro_real_aa')}% a.a.\n"
                 "_Entenda com /aula selic e /aula juro real._")

    def cmd_aula(self, args: str) -> None:
        lesson = lessons.by_slug_or_topic(args.strip()) if args.strip() else lessons.pick({"sempre"})
        if not lesson:
            self.say("Não achei essa aula. Temas: " + ", ".join(lesson.slug[3:] for lesson in lessons.load_all()))
            return
        lessons.mark_taught(lesson)
        state.put("ultima_aula", {"slug": lesson.slug, "titulo": lesson.title, "pergunta": lesson.question(),
                                  "dia": clock.today().isoformat()})
        self.say(f"📚 *{lesson.title}*\n\n{lesson.body}")

    def cmd_ajuda(self, _args: str) -> None:
        self.say(HELP)

    # -- texto livre e IA com ferramentas
    def free_text(self, text: str) -> None:
        parsed = parse_free_expense(text)
        if parsed:
            value, desc = parsed
            state.put("pending_gasto", {"valor": value, "desc": desc})
            self.say(f"Lançar gasto de R$ {value:.2f}: \"{desc}\" (dinheiro)?",
                     [[("✅ Lançar", "gs|ok"), ("Cancelar", "gs|no")]])
            return
        answer = self.ask_llm(text)
        self.say(answer or "A IA está ocupada ou fora da janela da GPU agora (o pokemon-radar usa a placa nesse horário). "
                           "Tente um comando: /hoje, /parcelas, /pix, /metas, /simular ou /ajuda.")

    def tools(self) -> tuple[list[dict], dict]:
        today = clock.today()

        def gastos(inicio: str = "", fim: str = "") -> dict:
            ini = date.fromisoformat(inicio) if inicio else today.replace(day=1)
            end = date.fromisoformat(fim) if fim else today
            return {"inicio": str(ini), "fim": str(end), "por_categoria": F.by_category(self._splits(400, 0), ini, end)}

        def estabelecimentos(dias: int = 30) -> dict:
            return {"top": F.top_merchants(self._splits(int(dias), 0), today - timedelta(days=int(dias) - 1), today)}

        impl = {
            "gastos_por_categoria": gastos,
            "maiores_estabelecimentos": estabelecimentos,
            "parcelas_futuras": lambda: {"parcelas": F.installments_ahead(self._splits(30), today, 12)},
            "custo_pix_credito": lambda: {"pix": F.pix_credit_costs(self._splits(90, 400), days=90)},
            "mercado": lambda: market.snapshot(),
            "simular_parcelamento": lambda a_vista, parcela, n: finmath.cash_vs_installments(
                float(a_vista), float(parcela), int(n), market.snapshot().get("cdi_aa") or 0),
            "meses_para_meta": lambda valor, deposito_mensal: {"meses": finmath.months_to_goal(
                float(valor), float(deposito_mensal), finmath.annual_to_monthly(market.snapshot().get("cdi_aa") or 0))},
        }
        spec = [
            ("gastos_por_categoria", "Soma dos gastos por categoria num período (datas AAAA-MM-DD; padrão: mês atual).",
             {"inicio": {"type": "string"}, "fim": {"type": "string"}}, []),
            ("maiores_estabelecimentos", "Onde mais gastou nos últimos N dias.", {"dias": {"type": "integer"}}, []),
            ("parcelas_futuras", "Parcelas de cartão a vencer por mês.", {}, []),
            ("custo_pix_credito", "Taxas pagas com Pix no crédito nos últimos 90 dias.", {}, []),
            ("mercado", "Selic, CDI, IPCA, ciclo da Selic e juro real atuais.", {}, []),
            ("simular_parcelamento", "Compara preço à vista com n parcelas usando o CDI.",
             {"a_vista": {"type": "number"}, "parcela": {"type": "number"}, "n": {"type": "integer"}},
             ["a_vista", "parcela", "n"]),
            ("meses_para_meta", "Meses para juntar um valor guardando um depósito mensal.",
             {"valor": {"type": "number"}, "deposito_mensal": {"type": "number"}}, ["valor", "deposito_mensal"]),
        ]
        tools = [{"type": "function", "function": {"name": n, "description": d,
                                                    "parameters": {"type": "object", "properties": p, "required": r}}}
                 for n, d, p, r in spec]
        return tools, impl

    def ask_llm(self, question: str) -> str | None:
        if not self.llm:
            return None
        last = state.get("ultima_aula") or {}
        system = (
            "Você é o gestor financeiro pessoal do Akira, em português do Brasil, tom natural, sem travessão. "
            f"Hoje é {clock.today().isoformat()}. Para qualquer número, CHAME uma ferramenta; nunca invente nem calcule "
            "de cabeça. Responda curto (até 8 linhas) e, quando couber, explique o conceito por trás. "
            "Não recomende produto financeiro específico. "
            + (f"A última aula foi \"{last.get('titulo')}\" com a pergunta \"{last.get('pergunta')}\"; se a mensagem "
               "parecer uma resposta a ela, comente o raciocínio dele de forma socrática." if last else "")
        )
        tools, impl = self.tools()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": question[:800]}]
        for _ in range(4):
            msg = self.llm.chat_messages(messages, tools=tools, priority="agora")
            if not msg:
                return None
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip() or None
            messages.append(msg)
            for call in calls[:3]:
                fn = call.get("function", {})
                name, args = fn.get("name"), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                try:
                    result = impl[name](**args) if name in impl else {"erro": "ferramenta desconhecida"}
                except Exception as exc:  # noqa: BLE001 (erro da ferramenta volta para o modelo explicar)
                    result = {"erro": str(exc)[:200]}
                messages.append({"role": "tool", "content": json.dumps(result, ensure_ascii=False, default=str)})
        return None

    # -- botões
    def on_callback(self, cb: dict) -> None:
        data, cb_id = cb.get("data") or "", cb["id"]
        msg_id = (cb.get("message") or {}).get("message_id")
        kind, *rest = data.split("|")
        if kind == "gs":
            pend = state.get("pending_gasto")
            state.put("pending_gasto", None)
            tg.answer_callback(self.s.telegram_bot_token, cb_id)
            if rest[0] == "ok" and pend:
                self.create_cash_expense(pend["valor"], pend["desc"], None)
            return
        queue = state.get("review_queue", []) or []
        i = int(rest[0]) if rest else 0
        if i >= len(queue):
            tg.answer_callback(self.s.telegram_bot_token, cb_id, "Fila mudou; use /revisar de novo.")
            return
        it = queue[i]
        if kind in ("rv", "rs"):
            cat = it["sugestoes"][int(rest[1])]
            from app.review import apply_category
            apply_category(self.ff, {"group_id": it["group_id"], "transaction_journal_id": it["journal_id"],
                                     "tags": it["tags"], "notes": it["notes"]}, cat, "confirmado por você no bot")
            if kind == "rs":
                from app.classify import add_user_rule
                from app.ml import merchant_key
                key = merchant_key(it["desc"]) or it["desc"].lower()[:20]
                add_user_rule(key, cat)
            state.log_event("revisao", {"desc": it["desc"], "categoria": cat, "regra": kind == "rs"})
            tg.answer_callback(self.s.telegram_bot_token, cb_id, f"{cat} ✅")
        else:
            tg.answer_callback(self.s.telegram_bot_token, cb_id, "Pulado")
        self.show_review(i + 1, queue, msg_id)

    # -- laço
    def authorized(self, chat: dict, sender: dict) -> bool:
        """Só o dono, só em conversa privada. Grupo nunca: lá qualquer membro mandaria comandos."""
        return (chat.get("type") == "private"
                and str(chat.get("id")) == str(self.s.telegram_chat_id)
                and str(sender.get("id")) == str(self.s.telegram_user_id))

    def dispatch(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                cb = update["callback_query"]
                if self.authorized((cb.get("message") or {}).get("chat") or {}, cb.get("from") or {}):
                    self.on_callback(cb)
                else:
                    tg.answer_callback(self.s.telegram_bot_token, cb["id"], "Não autorizado")
                return
            msg = update.get("message") or {}
            if not self.authorized(msg.get("chat") or {}, msg.get("from") or {}):
                log.warning("mensagem ignorada de chat/usuário não autorizado")
                return
            text = (msg.get("text") or "").strip()
            if not text:
                return
            if text.startswith("/"):
                cmd, _, args = text[1:].partition(" ")
                handler = getattr(self, f"cmd_{cmd.split('@')[0].lower()}", None)
                if cmd in ("start",):
                    handler = self.cmd_ajuda
                (handler or self.cmd_ajuda)(args)
            else:
                self.free_text(text)
        except Exception as exc:  # noqa: BLE001 (o bot nunca morre por uma mensagem)
            log.exception("erro tratando update")
            try:
                self.say(f"⚠️ Deu erro: {type(exc).__name__}. Os detalhes ficaram no log do container.")
            except Exception:  # noqa: BLE001
                pass

    def run_forever(self) -> None:
        if not self.s.telegram_bot_token:
            log.warning("sem TELEGRAM_BOT_TOKEN: bot desligado")
            while True:
                time.sleep(3600)
        tg.set_commands(self.s.telegram_bot_token, MENU)
        offset = state.get("telegram_offset")
        log.info("bot ouvindo o Telegram")
        while True:
            try:
                updates = tg.get_updates(self.s.telegram_bot_token, offset)
            except Exception as exc:  # noqa: BLE001 (rede caiu: espera e tenta de novo)
                log.warning("getUpdates falhou: %s", exc)
                time.sleep(15)
                continue
            for up in updates:
                offset = up["update_id"] + 1
                state.put("telegram_offset", offset)
                self.pool.submit(self.dispatch, up)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    Bot().run_forever()
