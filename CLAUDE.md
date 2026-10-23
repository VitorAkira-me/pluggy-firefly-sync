# CLAUDE.md: especificação permanente do personal-finance

> Leia inteiro antes de mexer em qualquer coisa. Este arquivo descreve o que **existe e roda
> de verdade**. Entre conveniência e o que está aqui, este documento vence.
> Atualizado em 2026-10-19 (consolidação + gestor com IA local).

---

## 1. Propósito

Plataforma pessoal e self-hosted de finanças do Akira. Puxa as transações reais dos bancos
e cartões via Open Finance (Pluggy), limpa (internas e parcelas), grava no Firefly III,
classifica e conversa com ele pelo Telegram: de manhã diz **quanto dá pra gastar hoje,
quanto guardar e o que importa**, e um **gestor financeiro** (LLM local na GPU do KaviPC)
analisa, cobra e **ensina** com os números dele. Tudo funciona sem a IA; ela só melhora.

---

## 2. Arquitetura

```
 Bancos ──Open Finance──> Pluggy (Meu Pluggy)
                             │ (a cada 3h)
 ┌──────────── Umbrel (Raspberry Pi 4) ────────────────────────────────┐
 │ container personal-finance  (python -m app.main)                    │
 │   thread scheduler ─ sync ─ review(03h) ─ report+advisor(05h) ─ insights(dom) │
 │   thread bot ─ comandos, botões, texto livre, perguntas com ferramentas │
 │   /data (volume) ─ state.sqlite: faturas informadas, fila de revisão,│
 │                    aulas dadas, histórico do gestor, offset do bot  │
 │ Firefly III  <── fonte da verdade (só pela API)                     │
 │ pokemon-radar ── lê do Firefly o gasto da categoria "Pokémon TCG"   │
 └───────────────────────────┬─────────────────────────────────────────┘
                             │ Tailscale (100.x), porta 11434
 KaviPC RTX 3060 Ti ── Ollama qwen2.5:7b-instruct (dividido com o radar)
 Telegram @akira_financas_bot <──> Akira
```

| Componente | Papel |
|---|---|
| Pluggy | fonte dos dados bancários (só leitura) |
| Firefly III | livro-razão: contas, transações, categorias, orçamentos, cofrinhos |
| `app/` no container | sync, limpeza, classificação, relatório, gestor, bot, agendamento |
| `/data/state.sqlite` | **estado de automação**, nunca saldo nem transação |
| Ollama no KaviPC | classificação de sobra, texto do gestor, perguntas no bot |

**Fora do sistema** (ADR 0004): PostgreSQL auxiliar, FastAPI, n8n, Metabase, MCP. Arquivo
em `docs/arquivo/`.

---

## 3. Regras inegociáveis

1. **Firefly III é a única fonte da verdade.** O SQLite de `/data` guarda estado de
   automação (valores informados, filas, histórico do gestor), nunca um segundo livro-razão.
2. **Firefly só pela API oficial.**
3. **Saldo de conta corrente vem da Pluggy** (o do Firefly fica menor que o real).
4. **Idempotência**: `external_id = "pluggy:<id>"`; rodar duas vezes nunca duplica (teste obrigatório).
5. **Dinheiro não é contado duas vezes**: pagamento de fatura, Pix no crédito (lado da conta),
   PIX entre contas próprias e aplicação automática viram "Transferência interna" (tag
   `interno`) e ficam fora de todo relatório. Parcelas vão para o mês em que pesam (tag `parcela`).
6. **Segredos só em variável de ambiente** (Portainer). Nunca em código, log, commit, Telegram ou `.txt`.
7. **IA nunca faz conta.** Números vêm de Python (`app/facts.py`, `app/finmath.py`). O LLM
   explica, classifica com confiança ou escolhe qual ferramenta rodar.
8. **Checagem anti-alucinação**: frase do gestor com número que não está nos fatos é descartada.
9. **IA local e opcional.** Nada de API de IA externa com dado financeiro. PC desligado = segue sem IA.
10. **A GPU é dividida com o pokemon-radar.** Jobs em lote só usam a IA na janela
    (`config/llm_janelas.json`, espelho dos períodos "cheio" de `config/gpu_agenda.yaml` do
    radar). Fora dela, o bot só usa a IA se o modelo já estiver carregado. Uma chamada por vez.
11. **IA nunca é a primeira opção** de classificação; confiança < 0,80 só sugere (tag `revisar`).
12. **Descrição de banco é dado não confiável** (prompt injection): vai delimitada; categoria
    fora da lista permitida é descartada.
13. **Bot só responde ao dono em chat privado** (`chat.type == private`, `chat.id ==
    TELEGRAM_CHAT_ID` e `from.id == TELEGRAM_USER_ID`, também nos botões). Nenhuma ação
    financeira real (pagamento, transferência) é feita pelo sistema; só registros.
14. **Sem recomendação de produto financeiro específico** pelo gestor; ele ensina e organiza.
15. **Mapeamento de contas fora do Git** (tem ids financeiros): vem de `ACCOUNT_MAPPING_JSON`
    (Portainer) ou `/data/account_mapping.json`. No repositório só o `.example`.
16. **Zero manutenção manual como padrão.** Fatura aberta é calculada pela Pluggy
    (`app/invoices.py`); o `/fatura` é correção opcional que expira quando a fatura fecha.
    Pokémon TCG é **consumo** (desejo, com teto no orçamento), nunca "investimento".
    Pix recebido pequeno (até R$ 1.000) é venda de cartas (categoria de entrada "Venda de cartas",
    só reduz o líquido do Pokémon); Pix enviado para vendedor de cartas é ensinado com `/regra`.
    Meta de guardar é calculada (`app/savings.py`): `MONTHLY_SAVINGS_TARGET` é só o mínimo.
17. **O gestor roda na IA local.** Claude (CLI ou chat) é só para desenvolvimento e ajustes.
18. **Nome de pessoa nunca no repositório** (é público): regra com nome vai pelo `/regra` do bot (estado).
19. **Data é sempre a de Brasília** (`app/clock.py`); só o agendador usa UTC. Relatório agendado só
    sai com o dado de ontem completo ou, passado o limite, dizendo quais bancos estão atrasados.

---

## 4. Fluxo

```
sync (3/3h)      Pluggy -> normalize (internas, parcelas) -> dedupe em lote -> regra/histórico/ML -> Firefly
review (03:00)   tag "revisar" -> LLM (janela) -> aplica se conf >= 0,80; resto vira fila do /revisar
manhã (05:00+)   report: espera todas as conexões da Pluggy atualizarem hoje (dado até ontem, D-1),
                 tentando de hora em hora até 10:00; roda o sync antes se a Pluggy atualizou depois dele;
                 pode gastar hoje (30 dias), faturas, mês, sinais de preço, rodapé com a hora do dado
                 advisor: fatos + aula do dia -> LLM -> manchete, observações, ação, aula, pergunta
domingo (09:00)  insights: semana x média, fora do padrão, assinaturas prováveis
bot (sempre)     /hoje /gestor /fatura /gasto /revisar /regra /simular /metas /parcelas /pix /mercado /aula
                 texto livre: "gastei 40 no ifood" -> confirmar; perguntas -> LLM com ferramentas
saúde (04h/16h)  conexões da Pluggy: login, atraso > 36 h, auto-sync desligado, consentimento perto de vencer
falha 2x seguidas de um job -> alerta no Telegram
```

---

## 5. Classificação (ordem obrigatória)

| # | Etapa | Como | Aplica se |
|---|---|---|---|
| 0 | Interno | `normalize.detect_internal` (texto + pareamento de valor entre contas) | sempre |
| 1 | Regra | regras do bot ("📌 Sempre") + `config/category_rules.json` | sempre |
| 2 | Histórico | mesmo estabelecimento sempre na mesma categoria (>= 2x) | sem conflito |
| 3 | ML | Naive Bayes de n-gramas de caracteres, treinado a cada execução | prob >= 0,90 |
| 4 | LLM | Ollama, JSON validado, só na passada das 03:00 | conf >= 0,80 |
| 5 | Humano | `/revisar` com botões de sugestão | resto |

Cada decisão vai no campo `notes` da transação ("classificado por ... (conf ...)").

---

## 6. Estrutura

```
app/
  main.py         processo do container: thread scheduler + thread bot
  scheduler.py    horários, subprocesso por job, alerta de falha
  config.py       env + config/*.json
  state.py        SQLite em /data (kv, eventos, trava da IA)
  clients/        pluggy, firefly, telegram (envio, botões, polling), llm (janela, trava, tools)
  normalize.py    internas e parcelas
  classify.py     pipeline da seção 5 + regras do usuário
  ml.py           normalização de texto, chave de estabelecimento, Naive Bayes
  sync.py         Pluggy -> Firefly
  review.py       passada da madrugada e fila de revisão
  report.py       resumo diário e "pode gastar hoje"
  invoices.py     fatura aberta calculada (faturas fechadas + compras do ciclo + parcelas)
  savings.py      meta de guardar calculada: sobra típica de 3 meses x folga dos próximos 30 dias
  health.py       saúde das conexões da Pluggy
  freshness.py    quão atual está o dado de cada conexão (D-1), versão do dado para o cache das faturas
  clock.py        data e hora de Brasília (o container roda em UTC)
  price_signals.py câmbio, IPCA/Selic, combustível
  facts.py        fatos determinísticos (mês, parcelas, Pix no crédito, Pokémon, metas)
  finmath.py      juros, taxas equivalentes, VP, custo efetivo, metas
  market.py       séries do Banco Central com histórico e ciclo da Selic
  lessons.py      trilha de aulas (RAG sobre app/knowledge)
  knowledge/      15 aulas em Markdown (editáveis)
  advisor.py      gestor diário
  insights.py     análise semanal
  bot.py          bot de mão dupla
config/           account_mapping, category_rules (+internal, +categories), price_signals, llm_janelas
scripts/          validate_config, list_uncategorized, fix_history, remap_accounts (ids novos da Pluggy), diagnose (faturas), conferir (bot x banco)
tests/            pytest sem rede
docs/             arquitetura, ia-local, operacao, decisions/, arquivo/
```

---

## 7. Configuração (variáveis)

| Variável | Obrig. | Padrão / exemplo |
|---|---|---|
| `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET` | sim | |
| `FIREFLY_BASE_URL`, `FIREFLY_TOKEN` | sim | `http://127.0.0.1:30009` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | sim | |
| `TELEGRAM_USER_ID` | não | igual ao chat id num chat privado |
| `ACCOUNT_MAPPING_JSON` | sim* | JSON do mapeamento numa linha (*ou `/data/account_mapping.json`) |
| `MONTHLY_SAVINGS_TARGET` | não | `175.89`: só o **mínimo**; a meta do mês é calculada (`app/savings.py`) |
| `SAVINGS_RATE`, `SAFETY_BUFFER` | não | `0.5` (fração da sobra que vira meta), `0` |
| `REPORT_HOUR_UTC`, `REPORT_DEADLINE_HOUR_UTC` | não | `8` (05:00, primeira tentativa), `13` (10:00, sai mesmo com banco atrasado) |
| `REVIEW_HOUR_UTC`, `SYNC_EVERY_HOURS` | não | `6`, `3` |
| `INSIGHTS_WEEKDAY_UTC`, `INSIGHTS_HOUR_UTC` | não | `6` (domingo), `12` |
| `LLM_BASE_URL` | não | mesmo valor do `LLM_LOCAL_URL` do pokemon-radar (`http://100.x.y.z:11434`) |
| `LLM_MODEL`, `LLM_TIMEOUT` | não | `qwen2.5:7b-instruct`, `120` |

---

## 8. Deploy

- `CMD python -m app.main`. Sem cron (não herda as variáveis; parou o sistema de 24/09 a 19/10/2026).
- Portainer, método **Repository**, `pull_policy: build`. Hot-patch dentro do container é proibido.
- Rede `host`; volume **só** em `/data` (`personal_finance_data`). Nada montado em `/app`.
- Depois do primeiro deploy da versão consolidada: `python scripts/fix_history.py` (plano) e
  `--apply` para corrigir o histórico.

---

## 9. Testes

Sem rede, `pytest -q`. Obrigatórios: idempotência, sinal de cartão, internas (texto e
pareamento), parcelas, `next_occurrence`, "pode gastar hoje", classificação e ordem,
validação da saída do LLM, checagem de números do gestor, janela da GPU, finmath,
custo do Pix no crédito, parsing do bot, config. CI: pytest + ruff + validate_config.

---

## 10. Git

Prefixos `feat:`, `fix:`, `security:`, `docs:`, `refactor:`, `test:`, `chore:`. Antes de
commit: status, diff, procurar segredo, testes. **Nenhum push ou merge sem aprovação
explícita do Akira.** Nunca versionar `.env`, `env*.txt`, exports, zips.

---

## 11. Fases

| Fase | Entrega | Status |
|---|---|---|
| 1-2 | Sync, relatório, faturas, 30 dias, sinais de preço | feito (set/2026) |
| 3 | Consolidação, scheduler único, dedupe em lote, internas e parcelas | feito (main b69d5e7) |
| 4 | Gestor com IA local, bot de mão dupla, revisão com botões, aulas | feito (main b69d5e7) |
| 4.1 | Fatura automática, Black 50%, saúde da Pluggy, bot só privado, mapeamento fora do Git | feito (main 5dfecf8) |
| 4.2 | Meta de guardar calculada, pagamento por boleto no cartão, vendas de cartas, /regra, BCB parcial, relatório D-1 (espera a Pluggy), data de Brasília | a publicar |
| 5 | Orçamentos por categoria no Firefly, metas com prazo, fechamento mensal | próxima |
| 6 | Comparar modelos com as correções como conjunto de teste; alertas em tempo real | ideia |

Ao fechar uma fase: atualizar `docs/` e o CONTEXTO do Projeto.
