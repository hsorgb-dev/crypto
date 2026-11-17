"""Kommandozeile.

    python -m erechnung erstellen rechnung.json --firma firma.json --format zugferd -o ausgabe/
    python -m erechnung erstellen rechnung.json --firma firma.json --nur-pruefen
    python -m erechnung pruefen ausgabe/RE-1.xml          # offizielle Regeln (XSD + Schematron)
    python -m erechnung schema --typ anthropic            # Tool-Definition für KI-Werkzeuge
    python -m erechnung server --firma firma.json --port 8080
    python -m erechnung mcp --firma firma.json --ausgabe e-rechnungen/

Die Firmenstammdaten können auch über die Umgebungsvariable ERECHNUNG_FIRMA
angegeben werden.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .api import create_invoice, load_profile
from .checks import FORMATS


def _profile(args) -> dict:
    return load_profile(args.firma or os.environ.get("ERECHNUNG_FIRMA"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m erechnung", description="E-Rechnungen erzeugen und prüfen")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("erstellen", help="E-Rechnung aus JSON erzeugen")
    p.add_argument("datei", help="Rechnungsdaten als JSON ('-' für stdin)")
    p.add_argument("--firma", help="Firmenstammdaten (JSON)")
    p.add_argument("--format", "-f", default=None, help=f"{', '.join(FORMATS)} (Standard: xrechnung-ubl)")
    p.add_argument("--ausgabe", "-o", default=".", help="Zielverzeichnis")
    p.add_argument("--nur-pruefen", action="store_true", help="Nur prüfen und Summen zeigen")

    p = sub.add_parser("pruefen", help="XML/PDF gegen die offiziellen Regeln prüfen (lxml + saxonche nötig)")
    p.add_argument("dateien", nargs="+")

    p = sub.add_parser("schema", help="Tool-Definition ausgeben")
    p.add_argument("--typ", choices=["anthropic", "openai", "mcp", "json"], default="anthropic")

    p = sub.add_parser("server", help="HTTP-API starten")
    p.add_argument("--firma")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)

    p = sub.add_parser("mcp", help="MCP-Server über stdio starten")
    p.add_argument("--firma")
    p.add_argument("--ausgabe", default="e-rechnungen")

    args = parser.parse_args(argv)

    if args.cmd == "erstellen":
        raw = sys.stdin.read() if args.datei == "-" else Path(args.datei).read_text(encoding="utf-8")
        result = create_invoice(json.loads(raw), fmt=args.format, profile=_profile(args),
                                render_pdf=not args.nur_pruefen)
        for w in result.warnings:
            print(f"HINWEIS {w}", file=sys.stderr)
        if not result.ok:
            for e in result.errors:
                print(f"FEHLER  {e}", file=sys.stderr)
            return 1
        print(json.dumps(result.totals.as_dict(), ensure_ascii=False, indent=2))
        if not args.nur_pruefen:
            for path in result.save(args.ausgabe):
                print(f"geschrieben: {path}")
        return 0

    if args.cmd == "pruefen":
        from .validator import main as validate_main
        return validate_main(args.dateien)

    if args.cmd == "schema":
        from .tool import INPUT_SCHEMA, anthropic_tool, mcp_tool, openai_tool
        data = {"anthropic": anthropic_tool, "openai": openai_tool, "mcp": mcp_tool}.get(args.typ)
        print(json.dumps(data() if data else INPUT_SCHEMA, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "server":
        from .server import serve
        serve(_profile(args), args.host, args.port)
        return 0

    if args.cmd == "mcp":
        from .mcp_server import run
        run(_profile(args), Path(args.ausgabe))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
