from __future__ import annotations

import os

import pytest

# Settings are cached per process, so pin them before the app imports them and
# make sure no developer's real .env leaks into the test run.
os.environ.update(
    {
        "OLLAMA_BASE_URL": "http://ollama.test:11434",
        "OLLAMA_MODEL": "test-model",
        "OLLAMA_API_KEY": "",
        "ENABLE_HEURISTIC_FALLBACK": "true",
        # Pinned so a developer's real .env (which may point at the hosted API
        # in json mode) cannot change what these tests assert.
        "OLLAMA_FORMAT_MODE": "schema",
        "OLLAMA_THINK": "",
        "OLLAMA_TIMEOUT_SECONDS": "9",
        "MAX_CONTENT_CHARS": "6000",
    }
)

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
