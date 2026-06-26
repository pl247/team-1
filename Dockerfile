# Use slim Python base image for security and performance
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose the application port
EXPOSE 8742

# Set environment variables with defaults
ENV APP_PORT=8742 \
    LLM_BASE_URL=http://198.18.5.11:8000/v1 \
    LLM_MODEL=/ai/models/NVIDIA/Nemotron-3-120B/ \
    LLM_API_KEY=LLM \
    LLM_TIMEOUT_SECONDS=15 \
    DB_PATH=/data/downtime.db \
    SIMULATOR_ENABLED=true \
    SIMULATOR_INTERVAL_SECONDS=8

# Create volume for persistent SQLite database
VOLUME ["/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${APP_PORT}/health || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${APP_PORT}"]