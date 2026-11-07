FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps . && useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache/embeddings && chown -R app:app /home/app/.cache
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["uvicorn", "pedidos.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
