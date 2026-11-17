"""MCP-Server (Model Context Protocol) über stdio, ohne Zusatzpakete.

Eintrag z. B. in der MCP-Konfiguration eines KI-Clients:

    {"mcpServers": {"e-rechnung": {
        "command": "python", "args": ["-m", "erechnung", "mcp", "--firma", "/pfad/firma.json",
                                      "--ausgabe", "/pfad/e-rechnungen"]}}}

Erzeugte Dateien werden im Ausgabeverzeichnis gespeichert; das Werkzeug gibt
Pfade, Summen, Fehler und Hinweise zurück.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .tool import TOOL_NAME, handle_tool_call, mcp_tool

PROTOCOL_VERSION = "2025-06-18"


def _response(msg_id, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def handle(message: dict, profile: dict, output_dir: Path) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:  # Notification
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return _response(msg_id, {
            "protocolVersion": requested,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "erechnung", "version": __version__},
        })
    if method == "ping":
        return _response(msg_id, {})
    if method == "tools/list":
        return _response(msg_id, {"tools": [mcp_tool()]})
    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != TOOL_NAME:
            return _response(msg_id, error={"code": -32602, "message": f"Unbekanntes Werkzeug {params.get('name')}"})
        try:
            out = handle_tool_call(params.get("arguments") or {}, profile=profile, output_dir=output_dir,
                                   include_content=False)
        except Exception as exc:  # noqa: BLE001 - Fehler an den Client melden statt abzustürzen
            out = {"ok": False, "errors": [f"Interner Fehler: {exc}"]}
        return _response(msg_id, {
            "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}],
            "isError": not out.get("ok", False),
        })
    return _response(msg_id, error={"code": -32601, "message": f"Methode {method} nicht unterstützt"})


def run(profile: dict, output_dir: Path, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            reply = _response(None, error={"code": -32700, "message": "Parse error"})
        else:
            reply = handle(message, profile, output_dir) if isinstance(message, dict) else \
                _response(None, error={"code": -32600, "message": "Invalid Request"})
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
