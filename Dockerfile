FROM python:3.11-slim-bookworm

WORKDIR /app

# rapidocr / opencv / onnxruntime 에 필요한 시스템 라이브러리
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY . .

# 빌드 시 OCR 엔진·모델 로드 검증 (실패하면 배포 중단)
RUN python -c "from src.parse_overdue_rules_image import _get_rapid_ocr; _get_rapid_ocr(); print('OCR OK')"

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
