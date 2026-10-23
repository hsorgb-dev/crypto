"""Minimal MCP server over stdio (JSON-RPC 2.0, one message per line), standard library only.

Register it in an MCP client (Claude Desktop, Claude Code, …):
    {"mcpServers": {"e-rechnung": {"command": "python", "args": ["-m", "e_rechnung", "mcp"],
                                   "cwd": "/pfad/zum/repository"}}}
Created files go to E_RECHNUNG_AUSGABE (default: ./rechnungen)."""
import json
import os
import sys
import traceback
from pathlib import Path

from . import FORMATS, InvoiceError, create, totals, validate
from .schema import tool_definitions

PROTOCOL = '2025-06-18'


def _output_dir():
    folder = Path(os.environ.get('E_RECHNUNG_AUSGABE', 'rechnungen')).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _issues(issues):
    return [issue.as_dict() for issue in issues]


def call_tool(name, arguments):
    """Run one tool. Returns (result dict, is_error)."""
    if name == 'e_rechnung_erstellen':
        fmt = arguments.get('format', 'xrechnung-cii')
        try:
            result = create(arguments.get('invoice'), fmt)
        except InvoiceError as exc:
            return {'ok': False, 'message': 'Pflichtangaben fehlen oder sind falsch. Bitte ergänzen und erneut aufrufen.',
                    'issues': _issues(exc.issues)}, True
        target = _output_dir() / result.filename
        target.write_bytes(result.content)
        return {'ok': True, 'file': str(target), 'format': FORMATS[result.format][0],
                'totals': totals(result.invoice), 'warnings': _issues(result.warnings)}, False
    if name == 'e_rechnung_pruefen':
        try:
            invoice, issues = validate(arguments.get('invoice'), arguments.get('format', 'xrechnung-cii'))
        except InvoiceError as exc:
            return {'ok': False, 'issues': _issues(exc.issues)}, True
        return {'ok': not any(i.level == 'fehler' for i in issues), 'issues': _issues(issues),
                'totals': totals(invoice)}, False
    if name == 'e_rechnung_lesen':
        from .viewer import inspect
        path = Path(arguments.get('path', ''))
        if not path.is_file():
            return {'ok': False, 'message': f'Datei nicht gefunden: {path}'}, True
        try:
            data, issues = inspect(path.read_bytes())
        except InvoiceError as exc:
            return {'ok': False, 'issues': _issues(exc.issues)}, True
        return {'ok': not any(i.level == 'fehler' for i in issues), 'data': data, 'issues': _issues(issues)}, False
    return {'ok': False, 'message': f'Unbekanntes Werkzeug {name!r}.'}, True


def handle(message):
    """Answer one JSON-RPC message; returns None for notifications."""
    method, request_id = message.get('method'), message.get('id')
    if request_id is None:
        return None
    if method == 'initialize':
        version = message.get('params', {}).get('protocolVersion') or PROTOCOL
        result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'e-rechnung', 'version': '1.0.0'},
                  'instructions': 'E-Rechnungen (XRechnung, ZUGFeRD) erstellen, prüfen und lesen. Rechnungsdaten als '
                                  'JSON nach dem Schema von e_rechnung_erstellen übergeben.'}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': [{'name': tool['name'], 'description': tool['description'],
                             'inputSchema': tool['input_schema']} for tool in tool_definitions()]}
    elif method == 'tools/call':
        params = message.get('params', {})
        try:
            payload, is_error = call_tool(params.get('name'), params.get('arguments') or {})
        except Exception as exc:  # report to the client instead of ending the server
            traceback.print_exc(file=sys.stderr)
            payload, is_error = {'ok': False, 'message': f'Interner Fehler: {exc}'}, True
        result = {'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False, indent=1, default=str)}],
                  'structuredContent': payload, 'isError': is_error}
    else:
        return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': f'Unbekannte Methode {method}'}}
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def run(stdin=sys.stdin, stdout=sys.stdout):
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        else:
            reply = handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False, default=str) + '\n')
            stdout.flush()
