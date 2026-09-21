# Pluggy → Firefly III: sincronização automática + relatório diário

Automatiza duas coisas que hoje dependem de você lembrar de fazer manualmente:

1. **Sincronizar transações reais dos seus bancos/cartões para o Firefly III**, via Open Finance (Meu Pluggy, gratuito para uso pessoal).
2. **Enviar todo dia, por Telegram, quanto você pode gastar e quanto deve guardar hoje**, com base no saldo real e nas contas que ainda vão vencer no mês.

Roda como container Docker no Raspberry Pi/Umbrel, gerenciado pelo Portainer (não como script solto via cron do sistema — essa era a ideia original, mas não persiste entre atualizações do Umbrel).

## Status atual: sincronização real funcionando de ponta a ponta (21/09/2026)

- Código no GitHub: `https://github.com/VitorAkira-me/pluggy-firefly-sync` (repositório público, sem segredos — as chaves ficam só no Portainer).
- Stack `pluggy-firefly-sync` rodando no Portainer do Umbrel (`http://umbrel.local:9000`), **desanexada do Git**. Qualquer mudança precisa ser feita direto no container (Console do Portainer) ou reanexando a stack ao Git.
- Container: **Running**, sem crash-loop.
- **`account_mapping.json` preenchido com os 9 mapeamentos reais** (5 cartões + 4 contas correntes) — ver seção abaixo.
- **`python3 sync.py` testado de verdade e funcionando**: primeira rodada criou 29 transações reais no Firefly; segunda rodada confirmou deduplicação (0 criadas, 29 já existiam) — o `external_id` baseado no id da transação da Pluggy está funcionando como esperado.
- Cron dentro do container: sincroniza a cada 3h, relatório diário às 7h (ver `Dockerfile`).

### ⚠️ Pendente crítico: as correções de código abaixo só existem dentro do container rodando (hot-patch via Console do Portainer), **não estão no GitHub**. Se a stack for reconstruída (rebuild da imagem, reanexar ao Git e dar pull, etc.), o código volta pras versões antigas e quebra de novo. Precisa subir `pluggy_client.py`, `sync.py` e `account_mapping.json` atualizados pro GitHub assim que possível.

## Mapeamento de contas (account_mapping.json) — completo

| Pluggy | Firefly | Tipo |
|---|---|---|
| Nubank platinum | Nubank (id 11) | cartão |
| Mercado Pago cartão | Mercado Pago (id 13) | cartão |
| Itaú Click Múltiplo MC Plat | Itaú Click (id 5) | cartão |
| MASTER BLACK PRIME | Bradesco Black (id 7) | cartão |
| CASAS BAHIA VISA PLATINUM | Bradesco Casas Bahia (id 6) | cartão |
| Nu Pagamentos (conta) | Nubank Conta Corrente (id 29) | conta corrente |
| Mercado Pago (conta) | Mercado Pago Conta Corrente (id 30) | conta corrente |
| itau (conta) | Itau Conta Corrente (id 31) | conta corrente |
| Banco Bradesco (conta) | Bradesco Conta Corrente (id 32) | conta corrente |

Excluídos de propósito:
- MASTER BLACK PRIME duplicado (aparece em dois itens Pluggy, Bradesco e BradescoCard — é o mesmo cartão físico; só uma das duas contas é mapeada, pra não duplicar transações).
- Poupança do Bradesco — Akira ainda precisa validar se é conta real ou de teste antes de entrar na sincronização.

As 4 contas correntes (Nubank, Mercado Pago, Itaú, Bradesco) foram criadas do zero no Firefly (ids 29–32) porque não existiam antes. A conta genérica antiga "Conta corrente" (id 15) ficou obsoleta — era um controle manual de caixa que a integração com a Pluggy substitui automaticamente — e não é mais usada.

## Três bugs reais encontrados e corrigidos durante o teste com dados reais

1. **`GET /items` (listar todas as conexões) não existe na API da Pluggy** — por design, "Listing existing connections is not provided due to security reasons" (doc oficial). O código antigo tentava usar esse endpoint e sempre voltava 401. Corrigido trocando para `GET /items/{id}`, com o id de cada conexão copiado manualmente do Dashboard (dashboard.pluggy.ai → aplicação → Itens Conectados) uma vez só.
2. **`GET /transactions` (paginação por página) foi descontinuado pela Pluggy** — retorna `410 Gone`. A doc oficial já marca esse endpoint como "List by Page (deprecated)". Corrigido trocando para `GET /v2/transactions` (paginação por cursor).
3. **Sinal invertido nas transações de cartão de crédito.** Na Pluggy, conta corrente segue o padrão "negativo = despesa, positivo = receita" — mas cartão de crédito vem ao contrário: positivo é compra (aumenta a dívida), negativo é pagamento/estorno (reduz a dívida). O `sync.py` assumia o mesmo padrão pra tudo, então toda compra no cartão virava "deposit" (receita) no Firefly, e todo pagamento de fatura virava "withdrawal" (despesa) — exatamente invertido. Corrigido adicionando `"is_credit_card": true/false` em cada entrada do `account_mapping.json`; o `sync.py` inverte o sinal antes de decidir o tipo da transação quando a conta é cartão. As 21 transações que tinham sido criadas erradas nos 5 cartões foram apagadas e recriadas certas — confirmado manualmente (ex: compra na Terabyteshop no Nubank agora aparece como "withdrawal", como deveria).

## Outras duas pegadinhas do deploy (Docker/Portainer, já corrigidas)

1. **Sem `volumes:` no `docker-compose.yml`.** Montar `account_mapping.json`/`category_rules.json`/logs como volume quebra o container quando a stack é buildada via **Repository** (Git) — o Portainer não deixa os arquivos do repo soltos no host, só usa como contexto de build.
2. **`umbrel.local` não resolve de dentro do container** (sem suporte a mDNS na imagem `python:3.11-slim`). Corrigido usando `FIREFLY_BASE_URL=http://127.0.0.1:30009`.

## O que é seu, o que é meu

**Sua parte:**

1. Conta no Meu Pluggy com bancos/cartões conectados. ✅ Feito.
2. `CLIENT_ID`/`CLIENT_SECRET` da Pluggy cadastrados no Portainer. ✅ Feito.
3. Personal Access Token do Firefly cadastrado no Portainer. ✅ Feito.
4. `account_mapping.json` preenchido com os ids reais. ✅ Feito.
5. Validar se a poupança do Bradesco é conta real ou de teste (pra decidir se entra na sincronização). **Pendente.**
6. Ajustar `category_rules.json` conforme forem aparecendo transações sem categoria reconhecida (a maioria das 29 transações reais importadas ainda não tem categoria — é normal, o dicionário de regras começa pequeno).

**Minha parte (já entregue):**

- `pluggy_client.py`, `firefly_client.py`, `sync.py`, `daily_report.py`, `config.py`.
- Descoberta e correção dos três bugs reais de API acima.
- Mapeamento completo das 9 contas e criação das 4 contas correntes novas no Firefly.
- Teste real de ponta a ponta: sincronização rodou, criou transações corretas, dedup confirmada.
- **Pendente da minha parte**: subir o código corrigido pro GitHub (hoje só existe hot-patch no container).

## Limitações conhecidas

- **Categorização por palavra-chave** ainda vai errar bastante até o dicionário crescer.
- **"Quanto posso gastar hoje"** ainda é uma aproximação (sem orçamentos por categoria no Firefly).
- **Bradesco Black dividido com a Katarina**: o Open Finance traz o valor total da fatura; a divisão 50/50 continua sendo ajuste manual mensal.
- **Sinais de preço/notícia** (inflação, combustível etc.) ficam de propósito fora deste script, num agendador separado do Claude.

## Próximos passos

1. Subir `pluggy_client.py`, `sync.py` e `account_mapping.json` atualizados pro GitHub (crítico — sem isso, um rebuild da stack reverte os três bugs corrigidos).
2. Validar a poupança do Bradesco e decidir se entra no mapeamento.
3. Confirmar que o relatório diário do Telegram continua chegando certinho com dados reais agora que há transações de verdade no Firefly.
4. Ir ajustando `category_rules.json` com o tempo.
5. Opcional: apagar os Personal Access Tokens órfãos do Firefly (`pluggy-sync-script`, `pluggy-sync-script-2`).
