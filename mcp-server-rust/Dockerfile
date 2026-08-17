FROM rust:1.80 as builder
WORKDIR /usr/src/mcp-server-rust
COPY . .
# For now, just compile to see if it works. 
# RUN cargo install --path .

FROM debian:bookworm-slim
# COPY --from=builder /usr/local/cargo/bin/mcp-server-rust /usr/local/bin/mcp-server-rust
# CMD ["mcp-server-rust"]
CMD ["echo", "Rust Server Placeholder"]
