ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}
WORKDIR /app
ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid "${APP_GID}" app \
  && useradd --uid "${APP_UID}" --gid app --create-home --shell /usr/sbin/nologin app

COPY pyproject.toml pyproject.toml
COPY app app
RUN pip install --no-cache-dir .
RUN chown -R app:app /app

ENV SPEAKI_SERVICE_HOST=0.0.0.0
ENV SPEAKI_SERVICE_PORT=8006
ENV SPEAKI_SERVICE_PUBLIC_BASE_URL=http://127.0.0.1:8006
EXPOSE 8006

USER app
CMD ["sh", "-c", "uvicorn app.main:app --host ${SPEAKI_SERVICE_HOST} --port ${SPEAKI_SERVICE_PORT}"]
