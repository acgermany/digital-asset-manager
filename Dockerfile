FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy application
COPY inventory_service.py /app/

# Create directories for data and scanning
RUN mkdir -p /data /scan

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the service
ENTRYPOINT ["python", "/app/inventory_service.py"]
CMD ["--scan-path", "/scan", "--db-path", "/data/inventory.db", "--stats"]