FROM python:3.12.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./requirements.lock
RUN --mount=type=cache,id=harbor-market-pip,target=/root/.cache/pip,sharing=locked \
    for attempt in 1 2 3; do \
        python -m pip install --require-hashes --no-deps -r requirements.lock && exit 0; \
        if [ "$attempt" -eq 3 ]; then exit 1; fi; \
        sleep 5; \
    done

COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY app ./app

RUN chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"]

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
