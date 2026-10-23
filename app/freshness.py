"""Quão atual está o dado da Pluggy (o "D-1" do relatório).

A Pluggy atualiza cada conexão (item) sozinha 1 vez por dia, num horário dela
(`lastUpdatedAt` / `nextAutoSyncAt`). O relatório saía às 05:00 sem olhar isso: se o
banco só atualizava às 07:00, o resumo mostrava o saldo e as compras de ANTEONTEM, e o
cache diário das faturas congelava esse valor velho até o dia seguinte.

Agora:
- o relatório só sai quando TODAS as conexões atualizaram hoje (então o dado vai até
  ontem, o D-1), tentando de hora em hora até REPORT_DEADLINE_HOUR (horário de Brasília);
- passou do limite, sai assim mesmo, dizendo quais bancos estão atrasados;
- antes de calcular, roda o sync se a Pluggy atualizou depois do último sync;
- o cache das faturas é por "versão do dado" (horário da última atualização), não por dia.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import clock

log = logging.getLogger(__name__)


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(clock.BRT)
    except ValueError:
        return None


def connections(pluggy, mapping: dict) -> list[dict]:
    """Uma linha por conexão (item) usada no mapeamento: banco, última atualização e próxima."""
    by_item: dict[str, list[str]] = {}
    for pid, m in mapping.items():
        try:
            item_id = pluggy.get_account(pid).get("itemId")
        except Exception as exc:  # noqa: BLE001 (conta sem resposta vira "desconhecido", não derruba o relatório)
            log.warning("conta %s sem resposta da Pluggy: %s", pid[:8], exc)
            continue
        if item_id:
            by_item.setdefault(item_id, []).append(m["firefly_account_name"])
    out = []
    for item_id, names in by_item.items():
        try:
            item = pluggy.get_item(item_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("item %s sem resposta da Pluggy: %s", item_id[:8], exc)
            continue
        out.append({"banco": (item.get("connector") or {}).get("name") or item_id[:8], "contas": names,
                    "atualizado": _ts(item.get("lastUpdatedAt") or item.get("updatedAt")),
                    "proxima": _ts(item.get("nextAutoSyncAt")), "status": item.get("status")})
    return out


def stale(conns: list[dict], now: datetime | None = None) -> list[dict]:
    """Conexões que ainda não atualizaram hoje (horário de Brasília): o dado delas é de antes de ontem."""
    today = (now or clock.now()).date()
    return [c for c in conns if not c["atualizado"] or c["atualizado"].date() < today]


def version(conns: list[dict]) -> str:
    """Muda sempre que alguma conexão atualiza: chave do cache das faturas."""
    return "|".join(sorted(c["atualizado"].isoformat() for c in conns if c["atualizado"]))


def newest(conns: list[dict]) -> datetime | None:
    stamps = [c["atualizado"] for c in conns if c["atualizado"]]
    return max(stamps) if stamps else None


def line(conns: list[dict], now: datetime | None = None) -> str:
    """Rodapé do relatório: até quando vai o dado e quem está atrasado."""
    if not conns:
        return "🕒 Não consegui ver a atualização dos bancos na Pluggy."
    late = stale(conns, now)
    if not late:
        oldest = min(c["atualizado"] for c in conns)
        return f"🕒 Bancos atualizados hoje (o mais antigo às {oldest:%H:%M}): valores até ontem."
    parts = [f"{c['banco']} ({c['atualizado']:%d/%m %H:%M})" if c["atualizado"] else f"{c['banco']} (sem data)"
             for c in late]
    return "⚠️ Ainda sem atualizar hoje: " + ", ".join(parts) + ". Os valores desses bancos estão atrasados."
