# Arquitetura

Visão geral e regras: `CLAUDE.md` seções 2 a 5. Aqui, o detalhe de cada módulo.

| Módulo | Faz | Fala com |
|---|---|---|
| `app/clients/pluggy.py` | auth, contas, transações (cursor), faturas fechadas | Pluggy |
| `app/clients/firefly.py` | transações paginadas, criação, recorrências, categorias | Firefly |
| `app/clients/telegram.py` | envio com fallback sem Markdown e quebra em 4000 caracteres | Telegram |
| `app/clients/llm.py` | `chat_json` (com schema) e `chat_text`, checagem de disponibilidade | Ollama |
| `app/ml.py` | normalização de texto, chave de estabelecimento, Naive Bayes | nada |
| `app/classify.py` | pipeline regra → histórico → ML → LLM → revisão | llm |
| `app/sync.py` | Pluggy → Firefly com dedupe em lote | pluggy, firefly |
| `app/report.py` | resumo diário e cálculo do "pode gastar hoje" | pluggy, firefly, telegram |
| `app/price_signals.py` | câmbio (AwesomeAPI), IPCA/Selic (BCB SGS), combustível (ANP) | APIs públicas |
| `app/insights.py` | fatos semanais + texto da IA | firefly, llm, telegram |
| `app/normalize.py` | internas (texto e pareamento) e parcelas no mês certo | nada |
| `app/review.py` | IA nas pendências (03:00) e fila do /revisar | firefly, llm, estado |
| `app/facts.py` / `app/finmath.py` | fatos e matemática financeira determinísticos | nada |
| `app/market.py` | séries do BCB com histórico, ciclo da Selic, juro real | BCB, estado |
| `app/lessons.py` + `app/knowledge/` | trilha de aulas e escolha por gatilho | estado |
| `app/advisor.py` | gestor diário com checagem de números | todos acima, llm, telegram |
| `app/bot.py` | comandos, botões, texto livre, IA com ferramentas | firefly, estado, llm, telegram |
| `app/state.py` | SQLite em /data e trava da IA | disco |
| `app/scheduler.py` + `app/main.py` | agenda (subprocesso por job, alerta) e bot lado a lado | módulos acima |

## "Pode gastar hoje"

Para cada data futura com saída prevista (conta fixa ou fatura) dentro de 30 dias:

```
ritmo(c) = (saldo hoje + entradas até c - saídas até c - colchão) / dias até c
pode gastar hoje = max(0, menor ritmo(c))
```

Ou seja: o maior gasto diário constante que ainda deixa dinheiro pra cada compromisso,
contando o salário que cai antes dele. Recorrências pagas no cartão (assinaturas) são
excluídas porque já estão dentro da fatura.

## Requisições por execução

| Job | Antes (set/2026) | Agora |
|---|---|---|
| sync (N transações novas, 9 contas) | 9 Pluggy + N buscas Firefly + N criações | 9 Pluggy + ~2 páginas Firefly + N criações |
| report | igual | igual (saldo e faturas: 1 chamada por conta) |
