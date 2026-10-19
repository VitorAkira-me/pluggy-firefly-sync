"""Limpeza do lote da Pluggy antes de gravar: movimentações internas e parcelas.

Por que isso existe (achado no export de out/2026): o mesmo dinheiro era contado
duas vezes como gasto.
- Pagar a fatura do cartão pela conta corrente gerava uma "despesa" na conta e um
  "pagamento recebido" no cartão, além das compras que já estavam no cartão.
- Pix no crédito do Itaú gerava "Crédito liberado para Pix" + "Pix enviado com
  cartão" na conta corrente (soma zero) e a compra de verdade no cartão.
- PIX entre suas próprias contas (Bradesco -> Itaú) virava receita e despesa.
- Compras parceladas apareciam com TODAS as parcelas na data da compra
  (ex.: 10x de R$ 190 = R$ 1.900 num mês só).

Regras:
1. Interno por padrão de texto (config/category_rules.json, lista "internal").
2. Interno por pareamento: entrada e saída do MESMO valor em contas mapeadas
   diferentes, com até PAIR_DAYS dias de distância (pagamento de fatura, PIX próprio).
3. Parcelas: quando a mesma compra aparece com vários números de parcela no lote,
   a parcela k vai para a data da compra + (k - menor parcela) meses, que é o mês em
   que ela de fato pesa na fatura. Com `creditCardMetadata` da Pluggy, usa a data da
   compra informada pelo banco.

Movimentação interna continua indo para o Firefly (auditoria), mas com a categoria
INTERNAL_CATEGORY e a tag "interno", e todo relatório ignora essa categoria.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date

from app.config import load_category_rules_file

INTERNAL_CATEGORY = "Transferência interna"
INTERNAL_TAG = "interno"
INSTALLMENT_TAG = "parcela"
PAIR_DAYS = 5
_INST = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*$")


def signed_amount(txn: dict, target: dict) -> float:
    """Valor do ponto de vista da conta: negativo = saiu dinheiro (ou aumentou dívida do cartão)."""
    amount = float(txn["amount"])
    return -amount if target.get("is_credit_card") else amount


def _d(txn: dict) -> date:
    return date.fromisoformat(txn["date"][:10])


def internal_patterns() -> list[str]:
    return [p.lower() for p in load_category_rules_file().get("internal", [])]


def detect_internal(batch: list[tuple[str, dict, dict]]) -> dict[str, str]:
    """batch: [(pluggy_account_id, target, txn)]. Retorna {txn_id: motivo} das internas."""
    found: dict[str, str] = {}
    pats = internal_patterns()
    for _, _, txn in batch:
        desc = (txn.get("description") or txn.get("descriptionRaw") or "").lower()
        hit = next((p for p in pats if p in desc), None)
        if hit:
            found[txn["id"]] = f"texto '{hit}'"

    # o lado da entrada pode já ter sido marcado por texto ("pagamento recebido" no cartão): ele ainda
    # serve para achar o par na conta corrente ("Pagamento de Pix QR Code MERCADO PAGO", por exemplo)
    ins = [(acc, txn) for acc, tgt, txn in batch if signed_amount(txn, tgt) > 0]
    outs = [(acc, txn, tgt) for acc, tgt, txn in batch if signed_amount(txn, tgt) < 0 and txn["id"] not in found]
    used: set[str] = set()
    for acc_in, t_in in ins:
        v = round(abs(float(t_in["amount"])), 2)
        for acc_out, t_out, _ in outs:
            if t_out["id"] in used or acc_out == acc_in:
                continue
            if round(abs(float(t_out["amount"])), 2) == v and abs((_d(t_in) - _d(t_out)).days) <= PAIR_DAYS:
                reason = f"par de R$ {v:.2f} entre contas próprias"
                found.setdefault(t_in["id"], reason)
                found[t_out["id"]] = reason
                used.add(t_out["id"])
                break
    return found


def installment_number(txn: dict, is_card: bool) -> tuple[int, int] | None:
    meta = txn.get("creditCardMetadata") or {}
    if meta.get("installmentNumber") and meta.get("totalInstallments"):
        return int(meta["installmentNumber"]), int(meta["totalInstallments"])
    if not is_card:
        return None
    m = _INST.search(txn.get("description") or "")
    if not m:
        return None
    n, total = int(m.group(1)), int(m.group(2))
    if 1 <= n <= total and 2 <= total <= 48:
        return n, total
    return None


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def shift_installments(batch: list[tuple[str, dict, dict]]) -> dict[str, dict]:
    """{txn_id: {"date": nova_data, "n": k, "total": t}} para parcelas que devem mudar de mês."""
    groups: dict[tuple, list[tuple[dict, int, int]]] = defaultdict(list)
    out: dict[str, dict] = {}
    for acc, tgt, txn in batch:
        info = installment_number(txn, bool(tgt.get("is_credit_card")))
        if not info:
            continue
        n, total = info
        meta = txn.get("creditCardMetadata") or {}
        if meta.get("purchaseDate"):
            base = date.fromisoformat(meta["purchaseDate"][:10])
            out[txn["id"]] = {"date": _add_months(base, n - 1).isoformat(), "n": n, "total": total}
            continue
        stem = _INST.sub("", txn.get("description") or "").strip().lower()
        key = (acc, stem[:20], round(abs(float(txn["amount"])), 2), txn["date"][:10], total)
        groups[key].append((txn, n, total))
    for items in groups.values():
        nums = {n for _, n, _ in items}
        lowest = min(nums)
        for txn, n, total in items:
            new = _add_months(_d(txn), n - lowest) if len(nums) > 1 else _d(txn)
            out[txn["id"]] = {"date": new.isoformat(), "n": n, "total": total}
    return out
