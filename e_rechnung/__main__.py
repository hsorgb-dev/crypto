"""Kommandozeile: python -m e_rechnung <befehl> …

    erstellen rechnung.json [-f FORMAT] [-o DATEI]   E-Rechnung aus JSON erzeugen
    pruefen DATEI [-f FORMAT] [--offiziell]          JSON-Daten, XML oder PDF prüfen
    anzeigen DATEI [-o DATEI.html]                   XML/PDF als lesbare HTML-Seite
    umwandeln DATEI -f FORMAT [-o DATEI]             z. B. XRechnung-XML -> ZUGFeRD-PDF
    lesen DATEI                                      XML/PDF -> JSON (Eingabeformat)
    server [--host H] [--port P]                     Weboberfläche und HTTP-API
    mcp                                              MCP-Server (stdio) für KI-Assistenten
    schema                                           JSON-Schema der Eingabe
    validator-laden                                  offiziellen Validator einmalig herunterladen
"""
import argparse
import json
import sys
from pathlib import Path

from . import FORMATS, InvoiceError, create, totals, validate
from .model import Issue


def _print_issues(issues):
    for issue in issues:
        print(f'  {issue}')


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise InvoiceError([Issue('fehler', str(path), f'Kein gültiges JSON: {exc}.')]) from None


def _is_json(path):
    return Path(path).suffix.lower() == '.json'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m e_rechnung', description='E-Rechnungen erstellen, prüfen, anzeigen.',
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    formats = ', '.join(FORMATS)

    p = commands.add_parser('erstellen', help='E-Rechnung aus JSON erzeugen')
    p.add_argument('json')
    p.add_argument('-f', '--format', default='xrechnung-cii', help=f'{formats} (Standard xrechnung-cii)')
    p.add_argument('-o', '--out', help='Zieldatei oder Ordner')

    p = commands.add_parser('pruefen', help='JSON, XML oder PDF prüfen')
    p.add_argument('file')
    p.add_argument('-f', '--format', default='xrechnung-cii', help='Zielformat bei JSON-Eingabe')
    p.add_argument('--offiziell', action='store_true', help='zusätzlich mit dem offiziellen Validator (Java)')

    p = commands.add_parser('anzeigen', help='XML/PDF als HTML anzeigen')
    p.add_argument('file')
    p.add_argument('-o', '--out')

    p = commands.add_parser('umwandeln', help='E-Rechnung in ein anderes Format umwandeln')
    p.add_argument('file')
    p.add_argument('-f', '--format', required=True, help=formats)
    p.add_argument('-o', '--out')

    p = commands.add_parser('lesen', help='XML/PDF in das JSON-Eingabeformat lesen')
    p.add_argument('file')

    p = commands.add_parser('server', help='Weboberfläche starten')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)

    commands.add_parser('mcp', help='MCP-Server über stdio')
    commands.add_parser('schema', help='JSON-Schema ausgeben')
    commands.add_parser('validator-laden', help='Mustang-Validator herunterladen')

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except InvoiceError as exc:
        print('Die E-Rechnung kann so nicht erstellt werden:', file=sys.stderr)
        for issue in exc.issues:
            print(f'  {issue}', file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        print(f'Fehler: {exc}', file=sys.stderr)
        return 1


def _write(result, out):
    target = Path(out) if out else Path(result.filename)
    if target.is_dir():
        target = target / result.filename
    target.write_bytes(result.content)
    print(f'{FORMATS[result.format][0]}: {target}')
    print(f'  Zahlbetrag {result.invoice.due_amount} {result.invoice.currency}')
    if result.warnings:
        print('Hinweise:')
        _print_issues(result.warnings)


def _run(args):
    if args.command == 'erstellen':
        _write(create(_load_json(args.json), args.format), args.out)
    elif args.command == 'umwandeln':
        from .reader import read
        _write(create(read(Path(args.file).read_bytes()), args.format), args.out)
    elif args.command == 'lesen':
        from .reader import read
        print(json.dumps(read(Path(args.file).read_bytes()), ensure_ascii=False, indent=2))
    elif args.command == 'anzeigen':
        from .viewer import html, inspect
        target = Path(args.out or Path(args.file).with_suffix('.html').name)
        target.write_text(html(*inspect(Path(args.file).read_bytes())), encoding='utf-8')
        print(f'Ansicht: {target}')
    elif args.command == 'pruefen':
        return _check(args)
    elif args.command == 'server':
        from .server import serve
        serve(args.host, args.port)
    elif args.command == 'mcp':
        from .mcp_server import run
        run()
    elif args.command == 'schema':
        from .schema import SCHEMA
        print(json.dumps(SCHEMA, ensure_ascii=False, indent=2))
    elif args.command == 'validator-laden':
        from . import official
        official.download()
    return 0


def _check(args):
    path = Path(args.file)
    if _is_json(path):
        invoice, issues = validate(_load_json(path), args.format)
        content = None
    else:
        from .viewer import inspect
        content = path.read_bytes()
        data, issues = inspect(content)
        print(f'{data.get("syntax")} · {data.get("specification")}')
    errors = [i for i in issues if i.level == 'fehler']
    print('Inhaltliche Prüfung:', 'keine Fehler' if not errors else f'{len(errors)} Fehler')
    _print_issues(issues)
    if _is_json(path) and not errors:
        print(json.dumps(totals(invoice), ensure_ascii=False, indent=2))
    if args.offiziell:
        from . import official
        if content is None:
            if errors:
                return 2
            result = create(_load_json(path), args.format)
            content, suffix = result.content, Path(result.filename).suffix
        else:
            suffix = path.suffix.lower()
        report = official.validate(content, suffix)
        print('Offizielle Prüfung (KoSIT-Regeln, veraPDF):', 'gültig' if report['valid'] else 'UNGÜLTIG')
        for message in report['messages']:
            print(f'  {message["level"].upper()} {message["message"]}')
        if not report['valid']:
            return 2
    return 2 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
