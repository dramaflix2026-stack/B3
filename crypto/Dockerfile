FROM python:3.12-slim
WORKDIR /app
COPY crypto /app/crypto
ENV PYTHONUNBUFFERED=1 PORT=8080 CRYPTO_DATA_DIR=/data/crypto
CMD ["python","crypto/collector_service.py"]
