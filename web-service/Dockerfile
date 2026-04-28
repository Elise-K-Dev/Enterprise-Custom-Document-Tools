ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}
WORKDIR /app
ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    chromium \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid "${APP_GID}" app \
  && useradd --uid "${APP_UID}" --gid app --create-home --shell /usr/sbin/nologin app

COPY pyproject.toml pyproject.toml
COPY app app
RUN pip install --no-cache-dir .
RUN chown -R app:app /app

ENV WEB_SERVICE_HOST=0.0.0.0
ENV WEB_SERVICE_PORT=8004
ENV CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
EXPOSE 8004

USER app
CMD ["sh", "-c", "uvicorn app.main:app --host ${WEB_SERVICE_HOST} --port ${WEB_SERVICE_PORT}"]
