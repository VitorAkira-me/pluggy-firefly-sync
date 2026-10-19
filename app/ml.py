"""Classificador de categoria treinado no seu próprio histórico do Firefly.

Conceito: Naive Bayes multinomial sobre n-gramas de caracteres.
- n-gramas de caracteres ("ifo", "foo", "ood") pegam o "jeitão" da descrição
  mesmo quando o banco escreve diferente ("IFOOD *RESTAURANTE", "IFD*IFOOD").
- Naive Bayes conta quantas vezes cada n-grama aparece em cada categoria e,
  para uma descrição nova, soma log-probabilidades: a categoria com maior soma
  vence. A probabilidade final (softmax das somas) vira a "confiança".
- Suavização de Laplace (+1) evita probabilidade zero pra n-grama nunca visto.

Por que não scikit-learn: o modelo cabe em ~60 linhas, treina em milissegundos
com alguns milhares de transações e roda no Raspberry Pi sem acrescentar
~100 MB de numpy/scipy na imagem. Se um dia precisar de algo mais forte, a
interface (fit/predict) é a mesma do scikit-learn e a troca é local.

O modelo é re-treinado a cada execução do sync a partir do Firefly, então não
há arquivo de modelo para versionar nem volume para montar.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict

_NOISE = re.compile(
    r"\b(compra|pix|pagamento|pgto|pg|mp|debito|credito|cartao|parcela|transf|ted|doc"
    r"|de|da|do|qr|code|enviado|recebido|boleto|com)\b"
)


def normalize(text: str) -> str:
    """Minúsculas, sem acento, sem dígitos/símbolos e sem palavras genéricas de banco."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z ]+", " ", text)
    text = _NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def merchant_key(text: str) -> str:
    """Chave curta do estabelecimento: as duas primeiras palavras normalizadas."""
    return " ".join(normalize(text).split()[:2])


def _features(text: str, n_min: int = 3, n_max: int = 5) -> Counter:
    t = f" {normalize(text)} "
    return Counter(t[i : i + n] for n in range(n_min, n_max + 1) for i in range(len(t) - n + 1))


class NaiveBayesCategorizer:
    def __init__(self, min_examples_per_class: int = 3):
        self.min_examples = min_examples_per_class
        self.class_counts: Counter = Counter()
        self.feature_counts: dict[str, Counter] = defaultdict(Counter)
        self.total_features: Counter = Counter()
        self.vocab: set[str] = set()

    def fit(self, descriptions: list[str], labels: list[str]) -> NaiveBayesCategorizer:
        usable = Counter(labels)
        for desc, label in zip(descriptions, labels, strict=True):
            if usable[label] < self.min_examples:
                continue  # classe com poucos exemplos só gera chute
            feats = _features(desc)
            self.class_counts[label] += 1
            self.feature_counts[label].update(feats)
            self.total_features[label] += sum(feats.values())
            self.vocab.update(feats)
        return self

    @property
    def trained(self) -> bool:
        return len(self.class_counts) >= 2

    def rank(self, description: str, k: int = 3) -> list[tuple[str, float]]:
        """As k categorias mais prováveis com a probabilidade de cada uma (para sugestões no bot)."""
        if not self.trained:
            return []
        scores = self._scores(description)
        top = max(scores.values())
        total = sum(math.exp(s - top) for s in scores.values())
        ranked = sorted(((c, math.exp(s - top) / total) for c, s in scores.items()), key=lambda kv: -kv[1])
        return ranked[:k]

    def predict(self, description: str) -> tuple[str | None, float]:
        """(categoria, probabilidade). (None, 0.0) se o modelo não tiver dados."""
        if not self.trained:
            return None, 0.0
        ranked = self.rank(description, 1)
        return ranked[0]

    def _scores(self, description: str) -> dict[str, float]:
        feats = _features(description)
        n_docs = sum(self.class_counts.values())
        v = len(self.vocab)
        scores = {}
        for label, count in self.class_counts.items():
            score = math.log(count / n_docs)
            denom = self.total_features[label] + v
            fc = self.feature_counts[label]
            for f, k in feats.items():
                score += k * math.log((fc.get(f, 0) + 1) / denom)
            scores[label] = score
        return scores
