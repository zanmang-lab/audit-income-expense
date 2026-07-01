FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY . .

# OCR 모델을 이미지 빌드 시 받아 두어 첫 요청 지연을 줄인다.
RUN python -c "from src.parse_overdue_rules_image import _get_rapid_ocr; _get_rapid_ocr()"

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "scripts/start_cloud.sh"]
