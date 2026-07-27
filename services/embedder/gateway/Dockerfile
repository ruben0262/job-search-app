# Thin gateway image — no model, no torch/fastembed. Just FastAPI forwarding to TEI.
FROM python:3.12-slim

WORKDIR /app
COPY gateway/requirements.txt /app/gateway/requirements.txt
RUN pip install --no-cache-dir -r /app/gateway/requirements.txt

COPY gateway /app/gateway

EXPOSE 8080
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
