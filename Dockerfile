# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder - resolve and install dependencies into a self-contained virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.4 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Dependency layer first so application edits do not invalidate the install.
COPY pyproject.toml README.md ./
RUN poetry install --only main --no-root

COPY src/ ./src/
COPY mock_erp/ ./mock_erp/
RUN poetry install --only main

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/home/app/.cache/huggingface \
    DOCLING_CACHE_DIR=/home/app/.cache/docling

# libgl1 and libglib2.0-0 are OpenCV's runtime dependencies, pulled in by
# Docling's layout and table-structure models. poppler-utils and tesseract
# back the OCR path for scanned invoices.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-deu \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/
COPY mock_erp/ ./mock_erp/
COPY evaluation/ ./evaluation/
COPY alembic/ ./alembic/
COPY alembic.ini pyproject.toml README.md ./
COPY samples/ ./samples/
COPY scripts/ ./scripts/

RUN mkdir -p /app/data/documents /home/app/.cache \
    && chown -R app:app /app /home/app

USER app

# Warm the Docling layout/OCR model cache into the image. If this is skipped the
# models download on the first document parsed instead, which makes the first
# request slow and requires network access at runtime.
RUN docling-tools models download \
    || echo "Docling model prefetch skipped; models will download on first parse."

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "invoice_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
