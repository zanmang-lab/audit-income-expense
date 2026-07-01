FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
