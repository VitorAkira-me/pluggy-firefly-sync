# 0007. Automação sem manutenção manual, bot só privado e rede host

**Status:** aceita (2026-10-19)

## Decisões
1. **Fatura calculada**: com as faturas fechadas da Pluggy (`/bills`) e as compras do cartão,
   o sistema calcula a fatura aberta sozinho. `/fatura` vira correção opcional que expira no
   fechamento. O `manual_invoice` do mapeamento fica só como último recurso.
2. **Cartão dividido**: `share` (padrão 0,5 no Bradesco Black) entra no fluxo de caixa sem pergunta.
3. **Saúde da Pluggy**: 2x por dia, aviso no Telegram (1 por problema por dia) para login,
   atraso, auto-sync desligado e consentimento perto de vencer.
4. **Bot**: só chat privado, `chat.id` e `from.id` conferidos, inclusive nos botões
   (achado da revisão de segurança de 19/10: em grupo qualquer membro mandaria comandos).
5. **Mapeamento fora do Git** (histórico já reescrito pelo Akira): variável ou volume.
6. **Rede host mantida**: o container não abre porta (long polling) e precisa de
   127.0.0.1:30009 (Firefly) e da rota do Tailscale (Ollama). Trocar por bridge exigiria o IP
   do Pi na LAN e testar a rota 100.x pelo NAT; fica para quando houver motivo concreto.
7. **Pokémon TCG é consumo**: gestor trata como desejo com teto; aula de "negócio x pessoal"
   só por gatilho explícito.
8. **Gestor 100% na IA local**; Claude só em desenvolvimento.
