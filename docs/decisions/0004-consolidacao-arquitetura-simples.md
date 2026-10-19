# 0004. Consolidar num repositório simples e arquivar a "Sprint 1 enterprise"

**Status:** aceita (2026-10-19)

## Contexto
Existiam dois projetos paralelos:
1. `pluggy-firefly-sync`: ~600 linhas de Python num container, rodando de verdade desde set/2026.
2. `personal-finance` (pasta local): esqueleto com FastAPI, PostgreSQL auxiliar, Alembic, n8n,
   MCP e Metabase planejados. Nunca ligado a dados reais, e o roadmap dele dizia "Open Finance
   não planejado", justamente a peça que mais entrega valor.

Além disso, o GitHub estava atrás do container (código novo só existia por hot-patch).

## Decisão
O código que funciona vira o núcleo, reorganizado em `app/`, `config/`, `tests/`, `docs/`.
O esqueleto enterprise fica em `docs/arquivo/` como referência. As boas ideias dele
continuam valendo e foram trazidas para o CLAUDE.md novo: ordem de classificação com IA por
último, limiar de confiança 0,80, defesa contra prompt injection, auditoria de cada decisão,
idempotência com teste obrigatório.

## Consequências
- Menos peças pra manter no Raspberry Pi: 1 container em vez de 4 ou 5.
- Auditoria fica no campo `notes` de cada transação do Firefly, em vez de tabela própria.
- Se um dia o volume pedir banco auxiliar (ex.: milhares de transações por mês ou histórico
  de decisões da IA para treino), reabrir esta decisão.
