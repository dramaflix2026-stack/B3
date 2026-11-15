FROM python:3.12-slim
WORKDIR /app
COPY crypto /app/crypto
ENV PYTHONUNBUFFERED=1 PORT=8080 CRYPTO_DATA_DIR=/data/crypto
RUN python -m compileall -q crypto && PYTHONPATH=/app/crypto python -m unittest discover -s crypto -p "test_paper_v7.py" -q
CMD ["python","crypto/collector_service.py"]
