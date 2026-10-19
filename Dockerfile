FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-workspace

COPY app/ app/
COPY config/ config/
COPY scripts/ scripts/

# Usuário sem privilégio: o container não precisa de root pra nada.
RUN useradd --create-home --uid 1000 pf && mkdir -p /data && chown -R pf /app /data
USER pf

# Um processo: agendador + bot (app/main.py). Sem cron (CLAUDE.md seção 8). Estado em /data (volume).
CMD ["/app/.venv/bin/python", "-m", "app.main"]
