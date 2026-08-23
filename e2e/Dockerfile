FROM python:3.12-slim
RUN pip install --no-cache-dir \
    pyyaml \
    "opentelemetry-sdk>=1.39.1,<2" \
    "opentelemetry-exporter-otlp-proto-http>=1.39.1,<2"
WORKDIR /workspace
CMD ["python", "/plugin/e2e/run_e2e.py"]
