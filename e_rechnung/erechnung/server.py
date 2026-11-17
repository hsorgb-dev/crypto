"""Kleine HTTP-API (nur Standardbibliothek) zur Anbindung beliebiger Werkzeuge.

    python -m erechnung server --firma firma.json --port 8080

Endpunkte
    GET  /health                 Lebenszeichen
    GET  /schema?typ=openai      Tool-Definition (anthropic | openai | mcp | json)
    POST /rechnung               JSON-Rechnung → JSON mit xml / pdf_base64 / Summen
    POST /rechnung?download=1    JSON-Rechnung → Datei (XML bzw. ZUGFeRD-PDF) direkt

Ist die Umgebungsvariable ERECHNUNG_TOKEN gesetzt, wird ``Authorization:
Bearer <token>`` verlangt. Standardmäßig lauscht der Server nur auf 127.0.0.1.
"""
from __future__ import annotations

import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .api import create_invoice
from .tool import INPUT_SCHEMA, anthropic_tool, handle_tool_call, mcp_tool, openai_tool

MAX_BODY = 1_000_000


def make_handler(profile: dict, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "erechnung/1.0"

        def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            if not token:
                return True
            given = self.headers.get("Authorization", "")
            if hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
                return True
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "errors": ["Nicht autorisiert"]})
            return False

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/health":
                return self._json(HTTPStatus.OK, {"ok": True})
            if not self._authorized():
                return None
            if url.path == "/schema":
                kind = parse_qs(url.query).get("typ", ["json"])[0]
                schema = {"anthropic": anthropic_tool, "openai": openai_tool, "mcp": mcp_tool}.get(kind)
                return self._json(HTTPStatus.OK, schema() if schema else INPUT_SCHEMA)
            return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "errors": ["Unbekannter Pfad"]})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return None
            url = urlparse(self.path)
            if url.path != "/rechnung":
                return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "errors": ["Unbekannter Pfad"]})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "errors": ["Leerer oder zu großer Body"]})
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "errors": [f"Ungültiges JSON: {exc}"]})
            if not isinstance(data, dict):
                return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "errors": ["JSON-Objekt erwartet"]})
            query = parse_qs(url.query)
            if "format" in query:
                data["format"] = query["format"][0]
            if query.get("download", ["0"])[0] in ("1", "true"):
                result = create_invoice(data, profile=profile)
                if not result.ok:
                    return self._json(HTTPStatus.UNPROCESSABLE_ENTITY,
                                      {"ok": False, "errors": result.errors, "warnings": result.warnings})
                body, ctype = (result.pdf, "application/pdf") if result.pdf else (result.xml, "application/xml")
                return self._send(HTTPStatus.OK, body, ctype,
                                  {"Content-Disposition": f'attachment; filename="{result.filename}"'})
            out = handle_tool_call(data, profile=profile)
            return self._json(HTTPStatus.OK if out["ok"] else HTTPStatus.UNPROCESSABLE_ENTITY, out)

    return Handler


def serve(profile: dict, host: str = "127.0.0.1", port: int = 8080) -> None:
    token = os.environ.get("ERECHNUNG_TOKEN")
    httpd = ThreadingHTTPServer((host, port), make_handler(profile, token))
    print(f"E-Rechnung-API läuft auf http://{host}:{port} (Token {'aktiv' if token else 'nicht gesetzt'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
