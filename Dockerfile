FROM python:3.10-slim
WORKDIR /app
COPY crypto_quant/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["python", "-m", "uvicorn", "crypto_quant.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
