# 0006. Bot de mão dupla, estado em SQLite e gestor com IA local

**Status:** aceita (2026-10-19)

## Contexto
- Atualizar algo manual (valor de fatura) exigia editar JSON, commit e redeploy.
- O Akira quer receber menos ruído e conversar: lançar gastos, revisar categorias, perguntar.
- Quer que a IA seja um gestor que organiza e ensina, não só um classificador.
- A GPU é dividida com o pokemon-radar, que tem agenda própria.

## Decisão
- Bot do Telegram com long polling na mesma imagem (thread ao lado do agendador).
- Estado de automação em SQLite num volume `/data` (nunca em `/app`), sem virar livro-razão.
- Gestor = RAG (aulas em Markdown) + dados do BCB + fatos calculados + ferramentas.
  Sem fine-tuning. Checagem de números contra os fatos.
- Uso da GPU limitado à janela "cheio" do radar para jobs em lote; bot fora da janela só
  com modelo já residente.

## Consequências
- Faturas, regras e revisões passam a valer na hora, sem deploy.
- Um volume a mais para fazer backup (pequeno; perder só apaga anotações).
- Se o radar mudar a agenda da GPU, `config/llm_janelas.json` precisa acompanhar.
