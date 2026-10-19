# CLAUDE.md — Permanent Project Specification

> This file is the **permanent specification and source of rules** for the
> `personal-finance` project. Read it completely before any implementation.
> When in doubt, this document wins over convenience.

---

## 1. Purpose

A **self-hosted, automated personal finance platform**. It ingests financial
data from multiple sources, normalizes and deduplicates it, classifies
transactions using a deterministic-first pipeline (AI only as fallback), and
pushes the result into **Firefly III** — the single financial source of truth.

---

## 2. Architectural principle (non-negotiable)

| Component            | Role                                                            |
|---------------------|-----------------------------------------------------------------|
| **Firefly III**      | Financial truth. Ledger, accounts, transactions, categories, budgets. |
| **PostgreSQL (aux)** | Automation, staging, raw data, audit, rules, cache, AI decision history, forecasts. |
| **n8n**              | Orchestration, ingestion, enrichment, classification, alerts, sync. |
| **finance-api**      | Internal secure API layer (Python / FastAPI).                  |
| **personal-finance-mcp** | Standardized interface for AI agents (Claude / ChatGPT). READ-ONLY first version. |
| **Metabase**         | Dashboards & analytics.                                        |
| **AI (Claude / OpenAI)** | Interpretation & recommendation. Classification **fallback only**. |
| **Notion**           | Documentation & planning only. No longer a financial source.   |

**Hard rule:** the auxiliary PostgreSQL database must **never** become a second
financial ledger competing with Firefly III. It stores automation state and
intelligence, not authoritative balances.

Data flow for agents:

```
Claude / ChatGPT  ->  MCP  ->  finance-api  ->  internal services
```

The MCP **never** touches PostgreSQL directly.

---

## 3. Infrastructure context

- Server: **Umbrel** (home server).
- Already installed: **Firefly III**, **n8n**.
- Added by this project: **PostgreSQL (aux)**, **finance-api**, later Metabase,
  `personal-finance-mcp`, possibly Redis.
- Admin access preferred via **Tailscale**. Never expose PostgreSQL or an
  unauthenticated n8n to the internet. Future public services go behind a
  secure reverse proxy.

Nothing about the environment may be invented. Unknown IPs, hostnames,
ports, tokens, passwords and domains are **configuration**, supplied by the
operator via environment variables.

---

## 4. Transaction automation pipeline

```
Source
  -> Raw transaction
  -> Normalization
  -> Hash / deduplication
  -> Merchant matching
  -> Deterministic rules
  -> Historical classification
  -> Classification + confidence score
  -> AI only if still unresolved
  -> Human review when confidence is low
  -> Firefly III
  -> Audit log
```

### Classification order (must be respected)

1. Exact match
2. Merchant rules
3. Category rules
4. Historical classification
5. Regex
6. AI
7. Human review

**AI is never the first option.**

---

## 5. AI rules

AI is used only after every deterministic step fails. Before any data leaves
the perimeter it must be **sanitized**. Never send:

- CPF, passwords, tokens, API keys
- full card numbers, full account numbers
- any financial information not strictly required for the task

AI classification output must be **structured**:

```json
{
  "merchant": "Normalized name",
  "category": "Alimentação",
  "subcategory": "Restaurante",
  "transaction_type": "expense",
  "confidence": 0.94,
  "reason": "Description consistent with a restaurant."
}
```

Valid `transaction_type` values: `expense`, `income`, `transfer`,
`investment`, `refund`, `unknown`.

If `confidence < 0.80` → do **not** auto-apply a definitive classification.
Mark the record `needs_review`.

Also defend against **prompt injection** in ingested descriptions/notes.

---

## 6. Deduplication (critical requirement)

Every import must be **idempotent**. The same file imported twice must not
create duplicate transactions.

Build a **stable hash** from structured fields, e.g.:

- `source`
- `source_account`
- `transaction_date`
- `amount` (canonical, fixed precision)
- `normalized_description`
- `external_id` when available

Do not trust the raw description alone. When `external_id` is present it takes
precedence in identity.

---

## 7. Security (higher priority than convenience)

This project processes personal financial data.

- Secrets only via environment variables.
- Real `.env` is git-ignored. `.env.example` holds **placeholders only**.
- Never print secrets in logs, fixtures, or n8n JSON.
- Never version databases, dumps, backups, credentials, cookies, sessions.
- Principle of least privilege everywhere.
- Internal authentication between services.
- Strict input validation (Pydantic).
- Prepared statements / ORM only. No string-built SQL.
- Actively defend against: SQL injection, SSRF, command injection, path
  traversal, mass assignment, insecure deserialization, prompt injection.
- Safe CSV / OFX handling: size limits, timeouts, controlled retries,
  rate limiting where applicable.
- Firefly writes happen **only** through the official API, never its DB.

### Prepared for (not necessarily installed in Sprint 1)

Dependency scanning, SAST, secret scanning, SBOM — via Dependabot / Renovate /
Trivy / Semgrep / Gitleaks / pip-audit / Bandit / Syft / Grype.

---

## 8. Dependencies

Minimize them. Before adding a library:

1. Explain why it is necessary.
2. Prefer mature, well-maintained libraries.
3. Avoid obscure packages.
4. Pin versions.
5. Avoid abandoned projects.

---

## 9. Git rules

Never version: `.env`, `*.key`, `*.pem`, `secrets/`, `credentials/`,
`backups/`, database dumps, n8n credentials, tokens, cookies, session files,
real financial exports.

Branches (future): `main`, `develop`, `feature/*`, `fix/*`, `security/*`.

Commit prefixes: `feat:`, `fix:`, `security:`, `docs:`, `refactor:`,
`test:`, `chore:`. Small, descriptive commits.

Before **every** commit: `git status`, review the diff, scan for secrets,
run tests. No force pushes. **No commit is created without operator review.**
**No push to GitHub without explicit operator approval.**

---

## 10. Backups

Design from day one:

- PostgreSQL: daily backup, retention policy, **tested** restore.
- Firefly: database + relevant files.
- Future: encrypted, off-site copy, documented restore.

A backup that has never been restored is **not** a validated backup.

---

## 11. Observability

- Structured logs (JSON).
- Never log tokens, authorization headers, card data, credentials, sensitive
  bank data.
- Correlation / execution IDs where possible.
- Each import produces an `import_batch_id`.
- Each relevant workflow carries an `execution_id`.

---

## 12. Auditability

Every important automated decision must be auditable. Record: `source`,
`workflow`, `classifier`, `rule`, `confidence`, `timestamp`, sanitized input,
result, status.

Must be able to answer: *"Why was this transaction classified as Transport?"*

---

## 13. n8n

Workflows are versioned under `n8n/workflows/`:

```
01_import_transactions.json
02_enrich_transactions.json
03_firefly_sync.json
04_transport_forecast.json
05_daily_monitor.json
06_monthly_review.json
```

JSON must contain **no real credentials** — only documented credential names.
Every workflow needs: error handling, retries, an error branch, logging,
idempotency, deduplication.

---

## 14. Firefly III integration

- Official API only. Never write to Firefly's internal DB.
- Isolated Firefly client module.
- Implement: `health/about`, `accounts`, `transactions`, `categories`,
  `budgets`.
- Retry: exponential backoff, max attempts, never blindly repeat
  non-idempotent operations.
- The real Firefly instance is **not** connected until the operator supplies
  and explicitly approves the configuration.

---

## 15. finance-api (FastAPI)

- Modern Python, type hints, Pydantic, SQLAlchemy, Alembic, pytest.
- Layers: `routers`, `services`, `repositories`, `models`, `schemas`,
  `security`, `integrations`.
- No business logic inside routers.

---

## 16. PostgreSQL (auxiliary)

Database name: `finance_automation`.

Initial tables:

```
raw_transactions
transaction_enrichment
merchant_rules
category_rules
ai_classifications
automation_logs
transport_forecast
system_settings
import_batches
```

Use PK, FK, unique constraints, indexes, timestamps. Use `JSONB` only where
truly appropriate — not for everything.

---

## 17. Transport module (design now, build later)

Old *Vale-Transporte* was cancelled. On-site days: **Tue, Thu, Fri**.
Route: **Arujá → São Paulo** via EMTU bus **807** or **530**, then
Metro / CPTM. Round trip. Reference budget: **R$ 360 / month**.

Future storage: fares, daily cost, on-site days, holidays, bridge days,
vacations, TOP balance, monthly forecast, actual spend, budget balance,
savings vs. old VT, accumulated savings.

Not finished in Sprint 1, but the architecture (`transport_forecast` table,
`/finance_get_transport` MCP tool, `04_transport_forecast.json` workflow)
must allow it.

---

## 18. MCP

Built later. First version **READ-ONLY**. Planned tools:

```
finance_get_summary
finance_get_transactions
finance_get_budget
finance_get_transport
finance_get_subscriptions
finance_get_debts
finance_get_goals
finance_get_investments
```

MCP → finance-api → internal services. Never MCP → PostgreSQL.

---

## 19. Testing

Mandatory tests for critical functions: hashing, deduplication, parsing,
normalization, classification, transport, API, Firefly client.

Always include the case: **the same transaction imported twice → only one
persisted.**

---

## 20. Sprint scope control

### Sprint 1 delivers ONLY

Repo skeleton, this CLAUDE.md, robust `.gitignore`, `.env.example`,
docker-compose for *new* components only, auxiliary PostgreSQL, initial
schema, Alembic migrations, minimal FastAPI with `GET /health`,
`FireflyClient`, ENV configuration, a read-only Firefly connectivity check,
unit tests, `n8n/workflows/01_import_transactions.json` (fictitious CSV +
local API), docs (README, architecture, security, threat-model, data-model,
firefly-integration, n8n-guide), Makefile dev commands.

### Sprint 1 must NOT implement

Real AI / OpenAI / Anthropic calls, full MCP, Metabase, Open Finance, bank
integration, automatic email, automatic writes to the real Firefly, complex
dashboards, the complete transport module. Prepare interfaces only.

### On completion

Stop. Do not start Sprint 2. Present the full report defined in the project
brief and wait for approval.
