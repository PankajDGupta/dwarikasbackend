# Multi-stage build: Compile native dependencies
# Stage 1: Builder - Compile native dependencies like PostgreSQL client bindings
FROM python:3.14-slim as builder

WORKDIR /build

# Install build dependencies for native modules (psycopg2, cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml ./

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel && \
    pip install \
    django>=6.0.5 \
    djangorestframework>=3.17.1 \
    google-cloud-secretmanager>=2.16.0 \
    google-cloud-tasks>=2.13.0 \
    google-cloud-storage>=2.10.0 \
    google-cloud-documentai>=1.23.0 \
    'pyjwt[crypto]>=2.8.0' \
    cryptography>=41.0.0 \
    psycopg2-binary>=2.9.9 \
    django-cors-headers>=4.3.0 \
    gunicorn>=21.0.0

# Stage 2: Runtime - Lightweight production image
FROM python:3.14-slim

WORKDIR /app

# Install only runtime dependencies (PostgreSQL client library, libssl for crypto)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libssl3 \
    libffi8 \
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

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import http.client; conn = http.client.HTTPConnection('localhost:8080'); conn.request('GET', '/'); response = conn.getresponse(); exit(0 if response.status == 200 else 1)" || exit 1

# Run Django application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--worker-class", "sync", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "dwarikasbackend.wsgi:application"]


