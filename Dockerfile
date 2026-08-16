# Road Weather Monitoring System
# Multi-stage Dockerfile

FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY dashboard/ ./dashboard/

# Expose port for API
EXPOSE 8000

# Default command runs the API server
# --app-dir src is required: sibling imports in src/ are not resolvable from /app.
CMD ["python", "-m", "uvicorn", "api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]


# Collector service variant
FROM base as collector

CMD ["python", "src/collector.py"]
