ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app

ENV JUDGE_HOST=0.0.0.0
ENV JUDGE_PORT=8010

CMD ["sh", "-c", "uvicorn app:app --host ${JUDGE_HOST} --port ${JUDGE_PORT}"]

