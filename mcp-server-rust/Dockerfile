FROM rust:latest as builder
WORKDIR /usr/src/mcp-server-rust
RUN apt-get update && apt-get install -y pkg-config libssl-dev
COPY . .
RUN cargo install --path .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y ca-certificates libssl3 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/cargo/bin/mcp-server-rust /usr/local/bin/mcp-server-rust
EXPOSE 8080
CMD ["mcp-server-rust"]
