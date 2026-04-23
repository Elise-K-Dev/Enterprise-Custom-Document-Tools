FROM rust:1.95 as builder
WORKDIR /app

COPY Cargo.toml Cargo.toml
COPY Cargo.lock Cargo.lock
COPY src src
RUN cargo build --release --locked --bin document-service

FROM debian:bookworm-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libbz2-1.0 liblzma5 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/document-service /usr/local/bin/document-service
COPY templates /app/templates
COPY DB /app/DB
ENV DOCUMENT_SERVICE_HOST=0.0.0.0
ENV DOCUMENT_SERVICE_PORT=8001
EXPOSE 8001
CMD ["document-service"]
