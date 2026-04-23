FROM python:3.11-slim
WORKDIR /app

COPY pyproject.toml pyproject.toml
COPY app app
COPY legacy-md-indexer-main legacy-md-indexer-main
RUN pip install --no-cache-dir .

ENV PARSER_SERVICE_HOST=0.0.0.0
ENV PARSER_SERVICE_PORT=8002
EXPOSE 8002
CMD ["sh", "-c", "uvicorn app.main:app --host ${PARSER_SERVICE_HOST} --port ${PARSER_SERVICE_PORT}"]
