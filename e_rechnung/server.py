"""Web interface and HTTP API (standard library only).

    python -m e_rechnung server            -> http://127.0.0.1:8765

API for the company AI or other systems (JSON in, file or JSON out):
    POST /api/erstellen?format=zugferd     body: invoice JSON   -> file, or 422 {"issues": [...]}
    POST /api/pruefen?format=xrechnung-cii body: invoice JSON   -> {"ok", "issues", "totals"}
    POST /api/lesen                        body: XML or PDF     -> {"data", "issues"}
    POST /api/anzeigen                     body: XML or PDF     -> HTML page
    POST /api/umwandeln?format=zugferd     body: XML or PDF     -> file
    POST /api/offiziell                    body: XML or PDF     -> {"valid", "messages"} (needs Java + validator)
    GET  /api/schema, /api/tools, /api/beispiel
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import FORMATS, InvoiceError, create, totals, validate
from .model import Issue

HERE = Path(__file__).parent
MAX_BODY = 20 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = 'e_rechnung'

    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type, filename=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False, default=str), 'application/json; charset=utf-8')

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            raise InvoiceError([Issue('fehler', 'datei', 'Datei ist größer als 20 MB.')])
        return self.rfile.read(length)

    def _invoice_json(self):
        try:
            return json.loads(self._body() or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvoiceError([Issue('fehler', 'json', f'Kein gültiges JSON: {exc}.')]) from None

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            self._send(200, (HERE / 'web' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/api/schema':
            from .schema import SCHEMA
            self._json(200, SCHEMA)
        elif path == '/api/tools':
            from .schema import tool_definitions
            self._json(200, tool_definitions())
        elif path == '/api/beispiel':
            self._send(200, (HERE / 'beispiele' / 'rechnung.json').read_bytes(), 'application/json; charset=utf-8')
        elif path == '/api/formate':
            self._json(200, {key: value[0] for key, value in FORMATS.items()})
        else:
            self._json(404, {'error': 'Nicht gefunden'})

    def do_POST(self):
        url = urlparse(self.path)
        fmt = parse_qs(url.query).get('format', ['xrechnung-cii'])[0]
        try:
            if url.path == '/api/erstellen':
                result = create(self._invoice_json(), fmt)
                self._send(200, result.content, result.mime, result.filename)
            elif url.path == '/api/pruefen':
                invoice, issues = validate(self._invoice_json(), fmt)
                self._json(200, {'ok': not any(i.level == 'fehler' for i in issues),
                                 'issues': [i.as_dict() for i in issues], 'totals': totals(invoice)})
            elif url.path == '/api/lesen':
                from .viewer import inspect
                data, issues = inspect(self._body())
                self._json(200, {'data': data, 'issues': [i.as_dict() for i in issues]})
            elif url.path == '/api/anzeigen':
                from .viewer import html, inspect
                self._send(200, html(*inspect(self._body())), 'text/html; charset=utf-8')
            elif url.path == '/api/umwandeln':
                from .reader import read
                result = create(read(self._body()), fmt)
                self._send(200, result.content, result.mime, result.filename)
            elif url.path == '/api/offiziell':
                from . import official
                content = self._body()
                self._json(200, official.validate(content, '.pdf' if content[:5] == b'%PDF-' else '.xml'))
            else:
                self._json(404, {'error': 'Nicht gefunden'})
        except InvoiceError as exc:
            self._json(422, {'ok': False, 'issues': [i.as_dict() for i in exc.issues]})
        except RuntimeError as exc:
            self._json(503, {'ok': False, 'error': str(exc)})


def serve(host='127.0.0.1', port=8765):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f'E-Rechnung läuft auf http://{host}:{port}  (Beenden mit Strg+C)')
    if host not in ('127.0.0.1', 'localhost'):
        print('Achtung: ohne Anmeldung im Netz erreichbar. Nur in einem geschützten Netz so starten.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
