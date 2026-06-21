# Multi-stage build: Compile native dependencies
# Stage 1: Builder - Compile native dependencies like PostgreSQL client bindings
FROM python:3.14-slim as builder

WORKDIR /build

# Install build dependencies for native modules (psycopg2, cryptography, opencv, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest — drives the pip install below so pyproject.toml
# is always the single source of truth for package versions.
COPY pyproject.toml uv.lock ./

# Create virtual environment and install ALL dependencies from pyproject.toml.
# gunicorn is added separately here because it is a production server concern
# not listed as an app dependency in pyproject.toml.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel uv && \
    uv pip install --system -r pyproject.toml && \
    pip install gunicorn>=21.0.0

# Stage 2: Runtime - Lightweight production image
FROM python:3.14-slim

WORKDIR /app

# Install only runtime system libraries required by the installed packages:
#   libpq5          — psycopg2 PostgreSQL client
#   libssl3         — cryptography / pyjwt TLS operations
#   libffi8         — cffi bindings used by cryptography
#   libgl1-mesa-glx — opencv-python-headless (Document AI image preprocessing)
#   libglib2.0-0    — opencv-python-headless (glib dependency)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libssl3 \
    libffi8 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port for Cloud Run
EXPOSE 8080

# Health check — points to the actual /api/v1/health/ liveness endpoint (Spec #16).
# start-period=60s gives Gunicorn + DB connection pool time to initialise
# before the first check fires.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import http.client; conn = http.client.HTTPConnection('localhost:8080'); conn.request('GET', '/api/v1/health/'); response = conn.getresponse(); exit(0 if response.status == 200 else 1)" || exit 1

# Run Django application with Gunicorn.
# Worker tuning for a 2 vCPU Cloud Run instance: (2 × vCPU) + 1 = 5 workers.
# --threads 2       : additional concurrency per worker for I/O-bound request tails
# --max-requests    : recycle workers after N requests to prevent memory leaks
# --max-requests-jitter: randomise recycling to avoid thundering-herd restarts
# --graceful-timeout: allow in-flight requests to finish before hard kill
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "5", \
     "--worker-class", "sync", \
     "--threads", "2", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "50", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "dwarikasbackend.wsgi:application"]
