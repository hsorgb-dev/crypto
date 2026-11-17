"""Hilfsfunktionen für die XML-Erzeugung (nur Standardbibliothek)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

from .calc import money


def amount(value: Decimal) -> str:
    """Geldbetrag mit genau 2 Nachkommastellen (BR-DEC-xx)."""
    return f"{money(value):f}"


def number(value: Decimal) -> str:
    """Menge/Preis/Satz ohne Exponent und ohne überflüssige Nullen."""
    text = f"{value.normalize():f}"
    return text


def price(value: Decimal) -> str:
    """Einzelpreis: mindestens 2 Nachkommastellen, mehr nur wenn angegeben."""
    if value == value.quantize(Decimal("0.01")):
        return f"{value.quantize(Decimal('0.01')):f}"
    return number(value)


def sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = text
    return el


def opt(parent: ET.Element, tag: str, text: str | None, **attrs: str) -> ET.Element | None:
    """Element nur anlegen, wenn ein Wert vorhanden ist."""
    if text is None or text == "":
        return None
    return sub(parent, tag, text, **attrs)


def iso(d: date) -> str:
    return d.isoformat()


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def payment_terms_text(terms: str | None, skonto, structured: bool) -> str | None:
    """BT-20. Skonto für XRechnung im Format #SKONTO#TAGE=n#PROZENT=x.xx# (BR-DE-18)."""
    parts = [terms] if terms else []
    for s in skonto:
        pct = f"{s.percent.quantize(Decimal('0.01')):f}"
        if structured:
            line = f"#SKONTO#TAGE={s.days}#PROZENT={pct}#"
            if s.base_amount is not None:
                line += f"BASISBETRAG={amount(s.base_amount)}#"
            parts.append(line)
        else:
            parts.append(f"{pct.replace('.', ',')} % Skonto bei Zahlung innerhalb von {s.days} Tagen.")
    if not parts:
        return None
    text = "\n".join(parts)
    return text + "\n" if structured and skonto else text


def serialize(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)
