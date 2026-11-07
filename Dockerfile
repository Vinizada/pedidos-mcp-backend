FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps . && useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache/embeddings /home/app/uploads \
    && chown -R app:app /home/app/.cache /home/app/uploads
COPY alembic.ini ./
COPY migrations ./migrations

FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --require-hashes -r requirements-dev.txt
COPY scripts ./scripts
COPY tests ./tests
USER app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM base AS runtime
COPY scripts/smoke_mcp.py ./scripts/smoke_mcp.py
USER app
EXPOSE 8000
CMD ["uvicorn", "pedidos.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
