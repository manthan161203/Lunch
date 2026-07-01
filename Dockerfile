FROM python:3.12-slim

# neonize ships a prebuilt glibc shared library, so use the Debian slim image
# (not Alpine/musl). python-magic needs libmagic at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY config.py handlers.py utils.py main.py ./

# Persist runtime state (WhatsApp session DB + CSV/JSON data) on a volume so it
# survives container restarts. Overridable via .env / -e.
ENV SESSION_DB=/data/lunch-drc-bot \
    ORDERS_CSV_FILE=/data/orders.csv \
    SUMMARY_CSV_FILE=/data/summary.csv \
    POLLS_DB_FILE=/data/polls_db.json
RUN mkdir -p /data
VOLUME ["/data"]

CMD ["python", "main.py"]
