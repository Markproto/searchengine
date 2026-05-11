# Profoundd Search Engine - Docker Setup
# For local development or Docker-based deployment

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# - gcc/g++/cmake/make/libboost-dev: for native Python builds
# - ffmpeg: required by yt-dlp for audio extraction from videos
# - playwright/chromium system libs needed for the Wikipedia-watcher's
#   weekly page snapshots (see profoundd/trackers/wikipedia_watcher.py)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ cmake make libboost-dev \
    ffmpeg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# yt-dlp lives outside requirements.txt so we can bump it independently
# (it ships frequently to keep up with YouTube's churn)
RUN pip install --no-cache-dir 'yt-dlp>=2024.7.0'
# Playwright + Chromium for the Wikipedia change-watcher screenshots.
# Chromium adds ~300MB to the image; only invoked from the weekly cron.
RUN pip install --no-cache-dir 'playwright>=1.45,<2.0' && \
    playwright install --with-deps chromium && \
    rm -rf /root/.cache/pip

# Copy application
COPY . .

# Create data directory
RUN mkdir -p data

# Expose port
EXPOSE 5000

# Run with gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "--timeout", "300", "--graceful-timeout", "30", "profoundd.app:app"]
