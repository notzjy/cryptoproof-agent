FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY cryptoproof ./cryptoproof
COPY knowledge ./knowledge
COPY static ./static
RUN pip install --no-cache-dir .
ENV AGENT_MODE=demo RAG_MODE=sparse DATA_DIR=/app/data
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "cryptoproof.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
