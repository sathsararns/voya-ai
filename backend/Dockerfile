FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Keep scanned-PDF ingestion available on Linux as well as Windows.
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

RUN groupadd --gid 10001 voya && useradd --uid 10001 --gid voya --create-home voya
COPY --chown=voya:voya . .
USER voya

EXPOSE 8000

# A fresh Docker database needs the auth/preferences migrations before startup.
# exec lets Uvicorn receive Docker's shutdown signal directly.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn main:app --host 0.0.0.0 --port 8000"]
