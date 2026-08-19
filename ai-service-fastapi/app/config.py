from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment (or a local .env file).

    Ollama is used as an external API, not something this platform runs. The
    defaults target the hosted endpoint at https://ollama.com, which needs
    OLLAMA_API_KEY. Pointing OLLAMA_BASE_URL at a self-hosted daemon also works
    -- it is the same HTTP API -- but then set OLLAMA_FORMAT_MODE=schema and
    clear OLLAMA_THINK, which local models reject.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", protected_namespaces=()
    )

    ollama_base_url: str = "https://ollama.com"
    ollama_api_key: str | None = None
    ollama_model: str = "gpt-oss:20b"

    # mcp-client-python gives up on /analyze after 10s, so the upstream call has
    # to finish inside that budget and still leave room to fall back.
    ollama_timeout_seconds: float = Field(default=9.0, gt=0)

    # How to ask for JSON. "schema" sends the full JSON schema, which a local
    # daemon uses to constrain decoding token by token. The hosted API silently
    # ignores it (and is slower for it), so "json" asks for plain JSON mode and
    # leans on the prompt plus normalisation for the enum values.
    ollama_format_mode: Literal["schema", "json"] = "json"

    # Reasoning effort. On the hosted gpt-oss, "low" roughly halves latency
    # without changing the verdicts, which is what keeps responses inside the
    # orchestrator's budget. Accepts "low"/"medium"/"high", or "false" to
    # suppress thinking. Clear it for models that do not think at all -- they
    # reject the field outright with a 400.
    ollama_think: str | None = "low"
    ollama_num_ctx: int = Field(default=4096, gt=0)

    # Longest slice of email text handed to the model. Phishing tells live in the
    # headers and opening lines; the tail is mostly quoted threads and footers.
    max_content_chars: int = Field(default=6000, gt=0)

    # Largest request body accepted at all. Truncation happens only after the
    # whole body is buffered, so without this a 20MB POST is read into memory
    # and then thrown away. Generous next to max_content_chars, since the point
    # is to bound memory, not to police legitimate email size.
    max_request_bytes: int = Field(default=1024 * 1024, gt=0)

    # When the model is unreachable, times out, or answers with something
    # unusable, answer from keyword heuristics instead of failing the request.
    enable_heuristic_fallback: bool = True

    log_level: str = "INFO"

    @property
    def think(self) -> str | bool | None:
        """`ollama_think` as the API wants it: a level, a bool, or absent."""
        raw = (self.ollama_think or "").strip().lower()
        if not raw or raw == "none":
            return None
        if raw in {"true", "false"}:
            return raw == "true"
        return raw

    @property
    def base_url(self) -> str:
        return self.ollama_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
