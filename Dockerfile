FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and configuration
COPY cover_art_cache/ ./cover_art_cache/
COPY uwsgi.ini scan_cache.py test_cover_art_cache.py .

# Create cache directory structure and log directory with proper permissions
RUN mkdir -p /var/cache/nginx/images/release /var/cache/nginx/images/release-group /var/log && \
    chmod -R 777 /var/cache/nginx/images && \
    chmod 777 /var/log

# Expose the port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application with uWSGI
CMD ["uwsgi", "--ini", "uwsgi.ini"]
