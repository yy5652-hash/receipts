FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY receipts ./receipts
COPY app ./app
COPY samples ./samples
RUN uv sync --frozen --no-dev
ENV PORT=8000 RECEIPTS_TRUST_PROXY=1
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-dev receipts serve --host 0.0.0.0 --port ${PORT}"]
