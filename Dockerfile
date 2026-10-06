# Multi-stage production Dockerfile for OpenSponsor Platform
FROM python:3.12-slim AS base

# System dependencies & security updates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    fastapi>=0.115.0 \
    "uvicorn[standard]>=0.30.0" \
    sqlalchemy>=2.0.30 \
    httpx>=0.27.0 \
    jinja2>=3.1.4 \
    pydantic-settings>=2.4.0 \
    gunicorn>=23.0.0

# Copy application source code and migrations
COPY app/ app/
COPY scripts/ scripts/
COPY start.py .

# Create non-root system user and data directories for persistent SQLite storage
RUN useradd -m -u 1000 appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /app /data

# Default environment variables
ENV HOST=0.0.0.0 \
    PORT=8080 \
    DB_URL=sqlite:////data/badge_platform.db \
    PYTHONUNBUFFERED=1

USER appuser

VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Production high-performance ASGI command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
