# API container for ECS Fargate (ARM64). One task only: the replay clock and its
# alert dispatch run in a background thread inside this process.
#   docker build --platform linux/arm64 -t glof-api .
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY archive ./archive
COPY reference ./reference

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
