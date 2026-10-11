# API container for ECS Fargate (ARM64). One task only: the replay clock and its
# alert dispatch run in a background thread inside this process.
#   docker build --platform linux/arm64 -t glof-api .
FROM python:3.12-slim

WORKDIR /app
# rasterio's bundled GDAL links against libexpat, which the slim image lacks
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY archive ./archive
COPY reference ./reference
# Built frontend (deploy.sh copies frontend/dist here). Served by the API when
# CloudFront is not used; empty otherwise.
COPY static ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
