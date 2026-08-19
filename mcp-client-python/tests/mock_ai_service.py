"""Fake ai-service-fastapi, for running mcp-client-python by hand.

ai-service-fastapi is fully implemented and can be run directly instead of
this; the stub only exists so the client can be driven without starting a
second service or holding an Ollama key. It answers POST /analyze with simple
keyword-based results matching the documented response shape. Stdlib only --
adds no new dependency to mcp-client-python.

This is a manual harness, not part of the test suite -- `pytest` mocks the AI
service with `respx` and needs no server running.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (stdlib method name)
        if self.path != "/analyze":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        content = body.get("content", "").lower()

        if "verify" in content or "suspend" in content:
            result = {"threat_level": "Phishing", "urgency": "Critical", "category": "Update"}
        elif "% off" in content or "sale" in content:
            result = {"threat_level": "Safe", "urgency": "Low", "category": "Promotion"}
        else:
            result = {"threat_level": "Safe", "urgency": "Normal", "category": "Work"}

        payload = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # quiet default access logging
        print(f"[MOCK AI SERVICE] {format % args}")


if __name__ == "__main__":
    print("Mock AI service (fake ai-service-fastapi) on http://127.0.0.1:8000")
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
