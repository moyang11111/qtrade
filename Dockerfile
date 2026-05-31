# QTrade Dockerfile
# Multi-stage build for optimized production image

# Base image
FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY pyproject.toml README.md ./

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install ".[all]"

# Copy source code
COPY src/ ./src/
COPY configs/ ./configs/
COPY examples/ ./examples/

# Copy additional files
COPY .env.example ./

# Create necessary directories
RUN mkdir -p data logs reports models

# Development stage
FROM base as dev
RUN pip install ".[dev]"
CMD ["bash"]

# Production stage
FROM base as production

# Create non-root user
RUN useradd -m -u 1000 qtrade && \
    chown -R qtrade:qtrade /app
USER qtrade

# Expose ports
EXPOSE 8000 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command
CMD ["python", "-m", "qtrade", "--help"]
