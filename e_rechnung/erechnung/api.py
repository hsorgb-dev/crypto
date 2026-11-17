"""Zentrale Funktion: JSON rein, E-Rechnung raus."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .calc import Totals, compute
from .checks import check, complete, normalize_format
from .cii import build_cii
from .model import InputError, Invoice, parse_invoice
from .ubl import build_ubl


@dataclass
class Result:
    ok: bool
    format: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    invoice: Invoice | None = None
    totals: Totals | None = None
    xml: bytes | None = None
    pdf: bytes | None = None

    @property
    def filename(self) -> str | None:
        if not self.invoice:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", self.invoice.number).strip("_") or "rechnung"
        return f"{safe}.pdf" if self.format == "zugferd" else f"{safe}.xml"

    def save(self, directory: str | Path) -> list[Path]:
        """Schreibt XML (und ggf. PDF) in ein Verzeichnis und gibt die Pfade zurück."""
        if not self.ok:
            raise ValueError("Rechnung hat Fehler und wurde nicht erzeugt")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stem = Path(self.filename).stem
        paths = []
        xml_path = directory / f"{stem}.xml"
        xml_path.write_bytes(self.xml)
        paths.append(xml_path)
        if self.pdf:
            pdf_path = directory / f"{stem}.pdf"
            pdf_path.write_bytes(self.pdf)
            paths.append(pdf_path)
        return paths


def merge(base: dict | None, override: dict | None) -> dict:
    """Tiefe Zusammenführung: Werte aus ``override`` gewinnen, Listen werden ersetzt."""
    result = copy.deepcopy(base) if base else {}
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_profile(path: str | Path | None) -> dict:
    """Firmenstammdaten (Verkäufer, Bankverbindung, Standard-Zahlungsziel) aus JSON."""
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def create_invoice(data: dict, fmt: str | None = None, profile: dict | None = None,
                   today: date | None = None, render_pdf: bool = True) -> Result:
    """Erzeugt eine E-Rechnung.

    data     Rechnungsdaten (siehe schema.py / README)
    fmt      'xrechnung-ubl' (Standard), 'xrechnung-cii' oder 'zugferd'
    profile  Firmenstammdaten; Angaben in ``data`` haben Vorrang
    """
    data = merge(profile, data)
    try:
        fmt = normalize_format(fmt or data.get("format"))
    except ValueError as exc:
        return Result(ok=False, format=str(fmt), errors=[str(exc)])
    try:
        invoice = parse_invoice(data, today=today)
    except InputError as exc:
        return Result(ok=False, format=fmt, errors=exc.errors)

    hints = complete(invoice, fmt)
    totals = compute(invoice)
    res = check(invoice, totals, fmt)
    result = Result(ok=res.ok, format=fmt, errors=res.errors, warnings=hints + res.warnings,
                    invoice=invoice, totals=totals)
    if not res.ok:
        return result

    if fmt == "xrechnung-ubl":
        result.xml = build_ubl(invoice, totals)
    elif fmt == "xrechnung-cii":
        result.xml = build_cii(invoice, totals, xrechnung=True)
    else:
        result.xml = build_cii(invoice, totals, xrechnung=False)
        if render_pdf:
            from .pdf import build_zugferd_pdf  # optionale Abhängigkeiten
            result.pdf = build_zugferd_pdf(invoice, totals, result.xml)
    return result
