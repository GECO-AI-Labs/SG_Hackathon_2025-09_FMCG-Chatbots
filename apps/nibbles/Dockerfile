# Build from the repository root so the shared dataset is in context:
#   docker build -f apps/nibbles/Dockerfile -t cashew4nuts-nibbles .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5001 \
    DATA_DIR=/app/data/Team_Cashew_Synthetic_Data

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first so application edits do not invalidate the layer.
COPY apps/nibbles/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/nibbles/ /app/
COPY data/Team_Cashew_Synthetic_Data/ /app/data/Team_Cashew_Synthetic_Data/

# Run unprivileged. The runtime directory is the only path that needs writing.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/data/runtime \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:5001/healthz || exit 1

# One worker, many threads. Nibbles keeps conversation state in process memory,
# so a second worker would lose a user's context between requests. Threads
# carry the concurrent streams. Move state to Redis before adding workers.
# --timeout 0 stops gunicorn killing a long streaming response mid-answer.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "16", \
     "--timeout", "0", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "app:app"]
