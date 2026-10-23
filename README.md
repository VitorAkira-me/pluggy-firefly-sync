# personal-finance

Finanças pessoais automáticas e self-hosted: **Pluggy (Open Finance) → Firefly III → Telegram**,
com classificação inteligente e análise semanal feitas por uma **IA local** (GPU de casa,
nada sai da rede). Regras e decisões completas: [`CLAUDE.md`](CLAUDE.md).

```
Bancos ─► Pluggy ─► sync (3/3h): internas, parcelas, regra → histórico → ML ─► Firefly III
                                                                              │
         revisão 03:00 (LLM local) · resumo + gestor 05:00 · semana (dom) ◄───┘
                                         │
                       Telegram ◄───────►│ bot: /fatura /gasto /revisar /hoje /simular, perguntas
```

## Como você recebe e como você atualiza

Tudo pelo Telegram (@akira_financas_bot). Poucas mensagens fixas, o resto sob demanda:

| Quando | Mensagem |
|---|---|
| 05:00 a 10:00 todo dia (assim que todos os bancos atualizam na Pluggy) | **Resumo** (pode gastar hoje, guardar, faturas, mês, sinais de preço) e **Gestor** (manchete, 2 a 4 observações, ação de hoje, aula do dia e uma pergunta) |
| domingo 09:00 | Análise da semana |
| quando precisa | Alerta se algum job falhar 2 vezes seguidas |

Você atualiza respondendo o bot:

| Comando | Para quê |
|---|---|
| `/fatura black 2450` | quanto você vai pagar de uma fatura (no Black, a sua parte); vale na hora, sem redeploy |
| `/gasto 35,90 almoço #Alimentação` ou "gastei 35,90 no almoço" | gasto em dinheiro |
| `/revisar` | classificar pendências com botões; "📌 Sempre" vira regra |
| `/hoje`, `/gestor` | resumo e gestor na hora |
| `/simular 1200 120 10` | à vista x parcelado com o CDI do dia |
| `/regra nome da pessoa #Pokémon TCG` | ensinar uma categoria (fica só no servidor, nunca no GitHub) |
| `/metas`, `/parcelas`, `/pix`, `/mercado`, `/aula juros` | meta de guardar do mês, consultas e aulas |
| qualquer pergunta | "quanto gastei com Pokémon este mês?" (IA com ferramentas, se a GPU estiver livre) |

## Subir do zero (Portainer, ~10 min)

1. **Pré-requisitos**: Firefly III rodando no Umbrel, conta no [Meu Pluggy](https://meu.pluggy.ai)
   com os bancos conectados, um bot do Telegram (@BotFather).
2. **Portainer → Stacks → Add stack → Repository**
   - URL: `https://github.com/VitorAkira-me/pluggy-firefly-sync`, branch `main`, compose `docker-compose.yml`.
3. **Environment variables** (os nomes estão em [`.env.example`](.env.example)):
   `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET`, `FIREFLY_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   Opcionais: `MONTHLY_SAVINGS_TARGET` (mínimo da meta), `SAVINGS_RATE` (padrão 0.5), `SAFETY_BUFFER`, `LLM_BASE_URL`, `LLM_MODEL`.
4. **Configurar contas.** Copie [`config/account_mapping.example.json`](config/account_mapping.example.json)
   para `config/account_mapping.json`, preencha os IDs da sua instalação e mantenha esse arquivo
   fora do GitHub. O arquivo real é ignorado pelo Git de propósito.
5. **Deploy.** Em Containers → `personal-finance` → Logs deve aparecer a linha `scheduler: ...`.
6. **Teste na hora** (Console do container):
   ```sh
   python -m app.sync --dry-run      # mostra o que entraria, sem gravar
   python -m app.report --no-send    # imprime o resumo sem mandar
   python -m app.insights --no-send  # imprime a análise semanal
   ```

Atualizar depois: mudou algo no GitHub → **Stacks → personal-finance → Pull and redeploy**.
Nunca edite arquivo dentro do container: some no próximo redeploy.

## Ligar a IA local (opcional)

O Ollama do worker GPU do pokemon-radar já serve o `qwen2.5:7b-instruct`. Basta pôr no
Portainer `LLM_BASE_URL` com o **mesmo valor do `LLM_LOCAL_URL` do pokemon-radar** (IP do
KaviPC no Tailscale, porta 11434). A IA respeita a agenda da GPU do radar
([`docs/ia-local.md`](docs/ia-local.md)). Sem isso tudo funciona, só com regras e ML.

## Primeira vez depois da consolidação

```sh
python scripts/fix_history.py           # mostra o que vai corrigir no histórico
python scripts/fix_history.py --apply   # corrige: internas, parcelas e regras
```

## Ajustes do dia a dia

| Quero... | Faço |
|---|---|
| Ver o que ficou sem categoria | `python scripts/list_uncategorized.py --days 30` |
| Ensinar uma categoria | corrigir no Firefly (o histórico aprende) ou nova regra em `config/category_rules.json` |
| Ligar conta/cartão novo | `python -m app.sync --list-accounts <item_id>` e adicionar em `config/account_mapping.json` |
| Mudar horário | `REPORT_HOUR_UTC` no Portainer (Brasília = UTC-3) |

## Estrutura

```
app/        código (clients/, sync, classify, ml, report, price_signals, insights, scheduler)
config/     regras editáveis (contas, categorias, limiares de preço)
scripts/    utilitários (validar config, listar sem categoria)
tests/      pytest sem rede
docs/       arquitetura, IA local, operação, decisões (ADRs) e arquivo
```

Desenvolvimento local:

```sh
uv sync
uv run pytest -q
uv run ruff check .
```

O projeto usa `uv` para resolver e fixar dependências no `uv.lock`; não é necessário
instalar `uv` dentro do ambiente virtual. Para atualizar dependências, use `uv lock --upgrade`
e revise o diff do lockfile antes de sincronizar.
Mais detalhes de operação e problemas conhecidos: [`docs/operacao.md`](docs/operacao.md).
