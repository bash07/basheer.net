FROM python:3.11-slim

WORKDIR /app

# System deps for httpx and crewai compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install any externally-managed domain modules (added via /system install).
# The file is also mounted at runtime so the bot can append to it on the fly.
COPY domain_modules.txt* ./
RUN if [ -f domain_modules.txt ]; then \
      grep -v '^\s*#' domain_modules.txt | grep -v '^\s*$' > /tmp/dm.txt || true; \
      if [ -s /tmp/dm.txt ]; then pip install --no-cache-dir -r /tmp/dm.txt; fi; \
    fi

COPY . .

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
