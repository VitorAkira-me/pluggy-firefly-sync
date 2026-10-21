"""Refaz o mapeamento de contas quando a Pluggy troca os ids (conexão refeita, app reativado).

Sintoma: `app.health` ou `app.sync` com "404 Not Found" em /accounts/<id> para todas as contas.
Causa: os ids de conta da Pluggy pertencem à conexão (item). Reconectou o banco ou a aplicação
foi recriada/reativada => ids novos. O lado do Firefly não muda.

O que este script faz:
1. lista as contas atuais dos itens informados (ids copiados de dashboard.pluggy.ai > sua
   aplicação > Itens conectados, ou do Meu Pluggy);
2. casa cada conta nova com a antiga pelo nome da Pluggy + tipo (cartão ou conta);
3. imprime o mapeamento novo numa linha, pronto para colar em ACCOUNT_MAPPING_JSON no Portainer,
   e a lista do que não casou (para você decidir).

Uso (Console do container):
    /app/.venv/bin/python scripts/remap_accounts.py <item_id> [<item_id> ...]
Nada é gravado; só imprime.
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.pluggy import PluggyClient  # noqa: E402
from app.config import account_mapping_source, load_settings  # noqa: E402


def _key(name: str) -> str:
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().strip()


def remap(old: dict, accounts: list[dict], activity=None) -> tuple[dict, list[str], list[str]]:
    """Devolve (mapeamento novo, contas antigas sem par, contas novas sem par).

    Nome repetido (ex.: o MASTER BLACK PRIME aparece nos itens Bradesco e BradescoCard): fica a
    conta com mais lançamentos recentes (`activity(conta) -> int`), que é a que de fato recebe os dados."""
    pool: dict[tuple, dict] = {}
    for a in accounts:
        k = (_key(a["name"]), a.get("type") == "CREDIT")
        if k in pool:
            print(f"  nome repetido: {a['name']} (fica a conta com mais lançamentos)", file=sys.stderr)
            if not activity or activity(a) <= activity(pool[k]):
                continue
        pool[k] = a
    used: set[str] = set()
    new_mappings, missing = [], []
    for m in old["mappings"]:
        hit = pool.get((_key(m.get("pluggy_account_name", "")), bool(m.get("is_credit_card"))))
        if hit and hit["id"] not in used:
            used.add(hit["id"])
            new_mappings.append({**m, "pluggy_account_id": hit["id"]})
        else:
            missing.append(m.get("firefly_account_name") or m.get("pluggy_account_name", "?"))
    extra = [f"{a['name']} ({a.get('type')}) id={a['id']}" for a in accounts if a["id"] not in used]
    return {**old, "mappings": new_mappings}, missing, extra


def main(item_ids: list[str]) -> None:
    if not item_ids:
        sys.exit(__doc__)
    s = load_settings()
    pluggy = PluggyClient(s.pluggy_client_id, s.pluggy_client_secret)
    accounts = []
    for iid in item_ids:
        item = pluggy.get_item(iid)
        found = pluggy.list_accounts(iid)
        print(f"Item {iid[:8]} ({(item.get('connector') or {}).get('name')}): {len(found)} conta(s)", file=sys.stderr)
        accounts += found
    source, old = account_mapping_source()
    cache: dict[str, int] = {}

    def activity(acc: dict) -> int:
        if acc["id"] not in cache:
            try:
                cache[acc["id"]] = len(pluggy.list_transactions(acc["id"], 45))
            except Exception:  # noqa: BLE001
                cache[acc["id"]] = 0
        return cache[acc["id"]]

    new, missing, extra = remap(old, accounts, activity)
    print(f"\nMapeamento atual veio de: {source}", file=sys.stderr)
    print(f"Casadas: {len(new['mappings'])} de {len(old['mappings'])}", file=sys.stderr)
    for name in missing:
        print(f"  sem par na Pluggy: {name}", file=sys.stderr)
    for e in extra:
        print(f"  conta nova não usada: {e}", file=sys.stderr)
    print("\nCole isto em ACCOUNT_MAPPING_JSON no Portainer:\n", file=sys.stderr)
    print(json.dumps(new, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main(sys.argv[1:])
