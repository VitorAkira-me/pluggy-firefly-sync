# 0005. IA local (Ollama no KaviPC) como camada opcional

**Status:** aceita (2026-10-19)

## Contexto
O Akira passou a ter uma RTX 3060 Ti (8 GB) rodando LLM localmente. 94% do gasto de um mês
chegou a ficar "Sem categoria" só com regras de palavra-chave.

## Decisão
- Classificação em camadas: regra → histórico → Naive Bayes (Pi) → LLM local → revisão humana.
- LLM só com saída estruturada (JSON Schema), categoria validada contra a lista do Firefly,
  limiar 0,80.
- Insights semanais: números em Python, texto pelo LLM.
- Nada de API de IA externa com dados financeiros.
- O sistema funciona inteiro com o PC desligado.

## Por que Naive Bayes puro e não scikit-learn
Treina em milissegundos com o volume atual, não acrescenta ~100 MB de numpy/scipy na imagem
do Pi e é um bom exercício de entender o modelo por dentro. Interface fit/predict igual à
do scikit-learn para trocar depois se precisar.
