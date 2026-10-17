# Jake Server (F9.1): solo libreria standard, nessuna dipendenza pip. Contesto di build = radice del repository
# (vedi docker-compose.yml e .dockerignore, che lascia entrare solo i file elencati qui sotto).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JAKE_SERVER_HOST=0.0.0.0 \
    JAKE_SERVER_PORT=8770

WORKDIR /app

# Solo cio' che il server importa davvero: core/ porta anche il codice Windows del desktop, che qui non serve.
COPY core/__init__.py core/version.py core/companion_guard.py ./core/
COPY config/release.json ./config/release.json
COPY jake_server/__init__.py jake_server/__main__.py jake_server/health.py ./jake_server/

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin jake
USER jake

EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('JAKE_SERVER_PORT', '8770'), timeout=2)"]

CMD ["python", "-m", "jake_server"]
