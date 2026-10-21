"""Bloco "Sinais de preço" do relatório diário: combustível, inflação/juros, câmbio,
e um veredito em semáforo sobre compra não essencial (eletrônicos, colecionáveis,
compras online). Substitui a tarefa "Sinais de preço e impulso de compra" que rodava
separada no Claude Cowork, sem IA: só fontes públicas e uma regra explícita e
configurável (config/price_signals.json). Adicionado em 23/09/2026.

Fontes:
- Câmbio: AwesomeAPI (JSON, gratuita): dólar de hoje e variação dos últimos 7 dias.
- Inflação/Selic: Banco Central, API SGS (JSON, gratuita, oficial): IPCA mensal
  (série 433), IPCA acumulado 12 meses (série 13522) e Selic meta (série 432).
- Combustível: ANP: só tem planilha .xlsx semanal, não tem API JSON (ver
  fetch_fuel_prices() para o motivo e um aviso importante sobre esta fonte).

Cada fonte é isolada (fetch_usd, fetch_fuel_prices, fetch_inflation_and_selic): se
uma falhar, as outras seguem normalmente e o bloco mostra "indisponível" só naquele
item: nunca derruba o relatório inteiro (ver build_block(), chamado com try/except
extra pelo daily_report.py por segurança)."""
import re
from datetime import date
from io import BytesIO

import requests

from app import state
from app.config import load_price_signals_config

BCB_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}?formato=json"
AWESOMEAPI_DAILY_URL = "https://economia.awesomeapi.com.br/json/daily/USD-BRL/7"
ANP_LISTING_URL = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/"
    "levantamento-de-precos-de-combustiveis-ultimas-semanas-pesquisadas"
)

IPCA_MENSAL_CODE = 433
IPCA_12M_CODE = 13522
SELIC_META_CODE = 432


DEFAULT_CONFIG = {
    "usd_week_change_yellow_pct": 0.75, "usd_week_change_red_pct": 1.5,
    "fuel_week_change_yellow_pct": 0.5, "fuel_week_change_red_pct": 1.5,
    "ipca_mensal_yellow_pct": 0.2, "ipca_mensal_red_pct": 0.3,
}


def load_config() -> dict:
    return {**DEFAULT_CONFIG, **{k: v for k, v in load_price_signals_config().items() if not k.startswith("_")}}


def load_cache() -> dict:
    """Último combustível lido com sucesso (fica no SQLite de estado, em /data, que sobrevive a redeploy)."""
    return state.get("price_signals_cache", {}) or {}


def save_cache(data: dict) -> None:
    state.put("price_signals_cache", data)


def _sgs_last(codigo: int) -> dict:
    resp = requests.get(BCB_SGS_URL.format(codigo=codigo, n=1), timeout=20,
                        headers={"User-Agent": "personal-finance/1.0 (uso pessoal)", "Accept": "application/json"})
    resp.raise_for_status()
    item = resp.json()[0]
    return {"data": item["data"], "valor": float(item["valor"])}


def fetch_inflation_and_selic() -> dict | None:
    """IPCA mensal, IPCA acumulado 12 meses e Selic meta, via API SGS do Banco
    Central (testada ao vivo em 23/09/2026: funciona, JSON simples [{"data":...,
    "valor":...}]). Se qualquer uma das três séries falhar, devolve None (as outras
    duas não são aproveitadas parcialmente, pra não montar uma frase incompleta)."""
    try:
        ipca_mensal = _sgs_last(IPCA_MENSAL_CODE)
        ipca_12m = _sgs_last(IPCA_12M_CODE)
        selic = _sgs_last(SELIC_META_CODE)
        return {
            "ipca_mensal_pct": ipca_mensal["valor"],
            "ipca_mensal_data": ipca_mensal["data"],
            "ipca_12m_pct": ipca_12m["valor"],
            "selic_pct": selic["valor"],
        }
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("BCB SGS indisponível: %s", exc)
        return None


def fetch_usd() -> dict | None:
    """Dólar de hoje e variação dos últimos 7 dias, via AwesomeAPI (documentada em
    docs.awesomeapi.com.br). O endpoint /daily/USD-BRL/7 devolve os últimos 7
    pregões, mais recente primeiro: a variação da semana é (hoje - 7 dias atrás) /
    7 dias atrás."""
    try:
        resp = requests.get(AWESOMEAPI_DAILY_URL, timeout=15)
        resp.raise_for_status()
        days = resp.json()
        if not days or len(days) < 2:
            return None
        today_price = float(days[0]["bid"])
        week_ago_price = float(days[-1]["bid"])
        if not week_ago_price:
            return None
        change_pct = (today_price - week_ago_price) / week_ago_price * 100
        return {"usd_today": today_price, "usd_week_change_pct": change_pct}
    except Exception:
        return None


def fetch_fuel_prices() -> dict | None:
    """Preço médio nacional de gasolina e etanol e variação vs a semana anterior, a
    partir da planilha semanal "resumo semanal LPC" da ANP.

    ATENÇÃO IMPORTANTE (23/09/2026): a ANP não tem API JSON pra isso, só publica um
    .xlsx por semana (link muda toda semana). O ambiente onde este código foi
    escrito não tem acesso de rede geral (só a alguns hosts liberados via uma
    ferramenta de busca), então NãO consegui baixar um arquivo real da ANP pra
    confirmar o layout exato da planilha (nome da aba, qual linha é o total
    "BRASIL", nomes exatos das colunas de gasolina/etanol). O parser abaixo
    (_parse_summary_xlsx) é uma melhor tentativa, escrita a partir da descrição
    pública da ANP ("preços médios semanais: Brasil, regiões, estados e
    municípios"), procurando por texto (BRASIL / GASOLINA / ETANOL) em vez de
    índices fixos, pra ser resiliente a pequenas variações: mas ISSO PRECISA SER
    VALIDADO contra um arquivo real antes de confiar no resultado. O jeito mais
    rápido: baixar o link abaixo (ANP_LISTING_URL tem a lista) e me mandar o .xlsx
    aqui no chat, pra eu conferir e corrigir o parser se precisar.

    Resiliência: se o fetch/parse falhar, cai pro último valor com sucesso salvo em
    price_signals_cache.json (marcado como "stale_since"), em vez de simplesmente
    sumir do relatório."""
    try:
        summary_url = _find_latest_summary_url()
        if not summary_url:
            raise ValueError("não achei o link da planilha mais recente na página da ANP")
        resp = requests.get(summary_url, timeout=30)
        resp.raise_for_status()
        parsed = _parse_summary_xlsx(resp.content)
        if parsed is None:
            raise ValueError("planilha baixada mas não achei a linha BRASIL / colunas de gasolina e etanol")

        cache = load_cache()
        previous = cache.get("fuel") or {}
        result = dict(parsed)
        for key in ("gasoline_avg", "ethanol_avg"):
            prev_val = previous.get(key)
            cur_val = result.get(key)
            if prev_val and cur_val is not None:
                result[f"{key}_change_pct"] = (cur_val - prev_val) / prev_val * 100
            else:
                result[f"{key}_change_pct"] = None

        cache["fuel"] = {
            "gasoline_avg": result.get("gasoline_avg"),
            "ethanol_avg": result.get("ethanol_avg"),
            "fetched_at": date.today().isoformat(),
        }
        save_cache(cache)
        return result
    except Exception:
        cache = load_cache()
        stale = cache.get("fuel")
        if not stale:
            return None
        return {
            "gasoline_avg": stale.get("gasoline_avg"),
            "ethanol_avg": stale.get("ethanol_avg"),
            "gasoline_avg_change_pct": None,
            "ethanol_avg_change_pct": None,
            "stale_since": stale.get("fetched_at"),
        }


def _find_latest_summary_url() -> str | None:
    """Acha, na página de listagem da ANP, o link do resumo semanal (não o de
    revenda por posto) com a data final mais recente."""
    resp = requests.get(ANP_LISTING_URL, timeout=30)
    resp.raise_for_status()
    matches = re.findall(r'href="([^"]*resumo_semanal_lpc_[\d-]+_[\d-]+\.xlsx)"', resp.text)
    if not matches:
        return None

    def end_date(url: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.xlsx$", url)
        return m.group(1) if m else ""

    latest = max(matches, key=end_date)
    return latest if latest.startswith("http") else "https://www.gov.br" + latest


def _parse_summary_xlsx(content: bytes) -> dict | None:
    """Procura, em qualquer aba, uma linha cujo texto seja "BRASIL" e, na mesma
    linha, os valores das colunas cujo cabeçalho contém "GASOLINA" e "ETANOL"."""
    import openpyxl

    wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    for sheet in wb.worksheets:
        gasoline_col = ethanol_col = header_row_idx = None
        for row in sheet.iter_rows(min_row=1, max_row=min(20, sheet.max_row)):
            for cell in row:
                text = str(cell.value or "").strip().upper()
                if "GASOLINA" in text and gasoline_col is None:
                    gasoline_col, header_row_idx = cell.column, cell.row
                if "ETANOL" in text and ethanol_col is None:
                    ethanol_col, header_row_idx = cell.column, cell.row
        if gasoline_col is None or ethanol_col is None:
            continue
        for row in sheet.iter_rows(min_row=(header_row_idx or 1) + 1, max_row=sheet.max_row):
            first_cell = row[0]
            if str(first_cell.value or "").strip().upper() != "BRASIL":
                continue
            gas_val = sheet.cell(row=first_cell.row, column=gasoline_col).value
            eth_val = sheet.cell(row=first_cell.row, column=ethanol_col).value
            if gas_val is None or eth_val is None:
                continue
            return {"gasoline_avg": float(gas_val), "ethanol_avg": float(eth_val)}
    return None


def compute_signal(usd: dict | None, fuel: dict | None, inflation: dict | None, config: dict) -> dict:
    """Regra do semáforo: cada métrica disponível é comparada a limiares
    amarelo/vermelho, e o PIOR resultado entre elas decide a cor final. Só uma
    ALTA dispara alerta (queda de preço/câmbio nunca é motivo de cautela: é bom
    pra quem vai comprar). Métrica indisponível é ignorada, não conta a favor nem
    contra. Em empate de nível, todas as razões daquele nível entram na frase."""
    level = 0  # 0 = verde, 1 = amarelo, 2 = vermelho
    reasons: list[tuple[int, str]] = []

    def bump(value, yellow, red, label):
        nonlocal level
        if value is None:
            return
        if value >= red:
            level = max(level, 2)
            reasons.append((2, f"{label} subiu {value:.2f}%"))
        elif value >= yellow:
            level = max(level, 1)
            reasons.append((1, f"{label} subiu {value:.2f}%"))

    if usd:
        bump(usd.get("usd_week_change_pct"), config["usd_week_change_yellow_pct"], config["usd_week_change_red_pct"], "dólar")
    if fuel:
        bump(fuel.get("gasoline_avg_change_pct"), config["fuel_week_change_yellow_pct"], config["fuel_week_change_red_pct"], "gasolina")
        bump(fuel.get("ethanol_avg_change_pct"), config["fuel_week_change_yellow_pct"], config["fuel_week_change_red_pct"], "etanol")
    if inflation:
        bump(inflation.get("ipca_mensal_pct"), config["ipca_mensal_yellow_pct"], config["ipca_mensal_red_pct"], "IPCA do mês")

    emoji = {0: "🟢", 1: "🟡", 2: "🔴"}[level]
    label = {
        0: "tranquilo pra uma compra pequena",
        1: "dá pra esperar mais uma semana",
        2: "evite compra por impulso hoje",
    }[level]
    worst = [r for lvl, r in reasons if lvl == level]
    reason_text = "; ".join(worst) if worst else "nenhum sinal de alta nos dados disponíveis hoje"
    return {"emoji": emoji, "label": label, "reason": reason_text}


def fmt_brl(value: float) -> str:
    """Mesmo formato do daily_report.py (R$ 1.234,56): duplicado aqui de propósito
    pra price_signals.py não depender de importar daily_report.py (evita import
    circular, já que é o daily_report.py quem importa este módulo)."""
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def format_price_block(usd: dict | None, fuel: dict | None, inflation: dict | None, signal: dict) -> str:
    """5 linhas: combustível, câmbio, inflação/Selic, e o veredito semáforo: pensado
    pra caber dentro da mesma mensagem do Telegram do relatório de saldo/gastos."""
    lines = ["📈 *Sinais de preço:*"]

    if fuel and fuel.get("gasoline_avg") is not None:
        gas_change = fuel.get("gasoline_avg_change_pct")
        eth_change = fuel.get("ethanol_avg_change_pct")
        gas_txt = f"{gas_change:+.1f}%" if gas_change is not None else "sem comparação"
        eth_txt = f"{eth_change:+.1f}%" if eth_change is not None else "sem comparação"
        stale = f" (dado de {fuel['stale_since']})" if fuel.get("stale_since") else ""
        lines.append(
            f"⛽ Gasolina {fmt_brl(fuel['gasoline_avg'])} ({gas_txt} na semana) | "
            f"Etanol {fmt_brl(fuel['ethanol_avg'])} ({eth_txt}){stale}"
        )
    else:
        lines.append("⛽ Combustível: indisponível hoje")

    if usd:
        lines.append(f"💵 Dólar {fmt_brl(usd['usd_today'])} ({usd['usd_week_change_pct']:+.1f}% em 7 dias)")
    else:
        lines.append("💵 Câmbio: indisponível hoje")

    if inflation:
        lines.append(
            f"📊 IPCA do mês: {inflation['ipca_mensal_pct']:.2f}% "
            f"(12 meses: {inflation['ipca_12m_pct']:.2f}%) | Selic: {inflation['selic_pct']:.2f}%"
        )
    else:
        lines.append("📊 Inflação/Selic: indisponível hoje")

    lines.append(f"{signal['emoji']} {signal['label']}: {signal['reason']}")
    return "\n".join(lines)


def build_block() -> str:
    """Ponto de entrada usado pelo daily_report.py."""
    config = load_config()
    usd = fetch_usd()
    fuel = fetch_fuel_prices()
    inflation = fetch_inflation_and_selic()
    signal = compute_signal(usd, fuel, inflation, config)
    return format_price_block(usd, fuel, inflation, signal)


if __name__ == "__main__":
    print(build_block())
