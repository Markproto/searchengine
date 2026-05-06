# Profoundd Search Engine - Docker Setup
# For local development or Docker-based deployment

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# - gcc/g++/cmake/make/libboost-dev: for native Python builds
# - ffmpeg: required by yt-dlp for audio extraction from videos
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ cmake make libboost-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# yt-dlp lives outside requirements.txt so we can bump it independently
# (it ships frequently to keep up with YouTube's churn)
RUN pip install --no-cache-dir 'yt-dlp>=2024.7.0'

# Copy application
COPY . .

# Create data directory
RUN mkdir -p data

# Expose port
EXPOSE 5000

# Run with gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "--timeout", "300", "--graceful-timeout", "30", "profoundd.app:app"]
