# Operação e problemas conhecidos

## Rotina

| Quando | O quê | Onde |
|---|---|---|
| Diário 05:00 | Ler resumo e gestor; responder a pergunta se quiser | Telegram |
| Quando chegar fatura | `/fatura <cartão> <valor>` (Black: a sua parte) | Telegram |
| Semanal | `/revisar` (2 minutos) | Telegram |
| Ao mudar código | PR no GitHub, merge, Pull and redeploy | GitHub + Portainer |

## Deploy e atualização

- Stack no Portainer pelo método **Repository** (Git), `pull_policy: build`.
- Variáveis de ambiente só no Portainer. Nunca em arquivo `.txt` no PC.
- **Proibido hot-patch** (editar arquivo dentro do container). Em set/2026 boa parte do
  código só existia dentro do container e quase se perdeu.

## Comandos úteis (Console do container)

```sh
python -m app.sync --dry-run
python -m app.report --no-send
python -m app.insights --no-send
python scripts/list_uncategorized.py --days 30
python scripts/validate_config.py
python scripts/fix_history.py [--apply]   # corrige internas, parcelas e regras no histórico
python -m app.review --no-llm             # remonta a fila do /revisar sem IA
python -m app.advisor --no-send           # imprime o gestor do dia
```

Estado (fora do Firefly) fica em `/data/state.sqlite` no volume `personal_finance_data`:
faturas informadas pelo bot, regras do "📌 Sempre", fila de revisão, aulas dadas,
histórico do gestor, saúde dos jobs e offset do Telegram. Apagar o volume não perde
dinheiro nem transação; perde só essas anotações.

## Limitações da Pluggy (não são bug)

1. **Sem atualização sob demanda** no Meu Pluggy: uma compra pode levar horas (já medido ~23h)
   até aparecer.
2. **Fatura aberta não existe na API**: `/bills` só traz faturas fechadas.
3. **`balance` de cartão muda de significado por banco.** Teste: se
   `balance == creditLimit - availableCreditLimit`, o banco está mandando o limite comprometido
   (inclui parcelas futuras). Em set/2026: Bradesco Black, Mercado Pago e Itaú Click incluem;
   Nubank e Casas Bahia não. Para esses três, o valor real da fatura vem de `manual_invoice`
   (informado pelo Akira) ou, quando existir, da resposta dele ao bot.
4. **`balanceCloseDate` vem vazio** nos 5 cartões; só o vencimento é confiável.
5. O mesmo cartão Bradesco Black aparece em dois itens (Bradesco e BradescoCard); só um está
   mapeado, de propósito.

## Problemas já resolvidos (não reintroduzir)

| Sintoma | Causa | Correção |
|---|---|---|
| Jobs agendados falhavam com 400 na Pluggy | cron não herda variáveis do Docker | `app.main` (agendador + bot) como processo principal |
| `python3: not found` no cron | PATH mínimo do cron | idem |
| Saldo menor que o real | `current_balance` do Firefly só soma o importado | saldo direto da Pluggy |
| Compras de cartão entrando como receita | sinal invertido na Pluggy | `is_credit_card` |
| "Pode gastar" zerado pelo salário | recorrência de entrada contada como conta | `direction` income/expense |
| Container quebrando ao subir | volume escondendo arquivos da imagem | sem volumes |
| `umbrel.local` não resolve | sem mDNS na imagem slim | `FIREFLY_BASE_URL=http://127.0.0.1:30009` |
| Sync lento no Pi | 1 busca no Firefly por transação | dedupe em lote (1 consulta por execução) |
| Sistema parado de 24/09 a 19/10/2026 sem aviso | container voltou ao cron; scheduler tinha sido iniciado à mão | `app.main` como CMD + alerta de job com 2 falhas |
| Gasto do mês quase dobrado | fatura paga, Pix no crédito e PIX próprio contados como gasto; parcelas futuras no mês da compra | `app/normalize.py` + `scripts/fix_history.py` |

## Segurança

- Token do Firefly ativo: `pluggy-sync-script-3` (os órfãos foram apagados em set/2026).
- Repositório público: nunca commitar `.env`, `env*.txt`, exports ou zips (o `.gitignore` cobre).
- Firefly, Portainer e Ollama só na rede local ou Tailscale.
