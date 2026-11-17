"""Anbindung an KI-Werkzeuge: Tool-Definition (JSON Schema) und Aufruf.

Die Definition gibt es in drei Formen:
    anthropic_tool()  Claude / Anthropic Messages API (``tools=[...]``)
    openai_tool()     OpenAI-kompatible Function Calling APIs
    mcp_tool()        Model Context Protocol (tools/list)

Der Aufruf ``handle_tool_call(arguments)`` liefert immer ein JSON-fähiges dict.
"""
from __future__ import annotations

import base64
from pathlib import Path

from .api import create_invoice
from .checks import FORMATS
from .model import TYPE_CODES, VAT_CATEGORIES

TOOL_NAME = "e_rechnung_erstellen"
TOOL_DESCRIPTION = (
    "Erstellt eine gesetzeskonforme elektronische Rechnung nach EN 16931: XRechnung 3.0 (UBL oder CII, "
    "Pflicht für öffentliche Auftraggeber, Leitweg-ID als buyer_reference) oder ZUGFeRD/Factur-X "
    "(PDF mit eingebetteter XML, üblich im B2B). Verkäuferdaten und Bankverbindung kommen aus den "
    "hinterlegten Firmenstammdaten und müssen nur angegeben werden, wenn sie abweichen. "
    "Erfinde niemals fehlende Pflichtangaben (Rechnungsnummer, Anschrift, USt-IdNr., IBAN, Leitweg-ID, "
    "Preise): Liefert das Werkzeug 'errors', frage den Nutzer gezielt nach genau diesen Angaben. "
    "Mit dry_run=true werden nur Prüfung und Beträge geliefert – nutze das, um dem Nutzer die Summen vor "
    "dem endgültigen Erstellen zur Bestätigung zu zeigen."
)

_ADDRESS = {
    "type": "object",
    "description": "Postanschrift",
    "properties": {
        "street": {"type": "string", "description": "Straße und Hausnummer"},
        "additional": {"type": "string", "description": "Adresszusatz"},
        "postal_code": {"type": "string", "description": "Postleitzahl"},
        "city": {"type": "string", "description": "Ort"},
        "country_code": {"type": "string", "description": "ISO-3166-Ländercode, Standard DE", "default": "DE"},
    },
    "required": ["city", "postal_code"],
}
_CONTACT = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"},
    },
}
_ELECTRONIC_ADDRESS = {
    "description": "Elektronische Adresse für den E-Rechnungsempfang. Text (E-Mail oder Leitweg-ID, Schema "
                   "wird erkannt) oder {value, scheme} mit scheme EM, 0204 (Leitweg-ID), 9930 (USt-IdNr.), 0088 (GLN).",
    "anyOf": [
        {"type": "string"},
        {"type": "object", "properties": {"value": {"type": "string"}, "scheme": {"type": "string"}},
         "required": ["value"]},
    ],
}


def _party(description: str, seller: bool) -> dict:
    props = {
        "name": {"type": "string", "description": "Vollständiger Name / Firma"},
        "address": _ADDRESS,
        "vat_id": {"type": "string", "description": "USt-IdNr., z. B. DE123456789"},
        "contact": _CONTACT,
        "electronic_address": _ELECTRONIC_ADDRESS,
        "id": {"type": "string", "description": "Kennung (z. B. Kundennummer)"},
        "registration_id": {"type": "string", "description": "Handelsregisternummer"},
    }
    if seller:
        props.update({
            "tax_number": {"type": "string", "description": "Steuernummer des Finanzamts"},
            "trading_name": {"type": "string", "description": "Handelsname, falls abweichend"},
            "legal_info": {"type": "string", "description": "Weitere rechtliche Angaben (z. B. Geschäftsführung)"},
            "kleinunternehmer": {"type": "boolean", "description": "Kleinunternehmer nach § 19 UStG (keine USt)"},
        })
    return {"type": "object", "description": description, "properties": props}


_ALLOWANCE = {
    "type": "object",
    "properties": {
        "amount": {"type": "number", "description": "Betrag netto"},
        "percent": {"type": "number", "description": "Alternativ Prozentsatz (mit base_amount)"},
        "base_amount": {"type": "number", "description": "Basisbetrag für percent"},
        "reason": {"type": "string", "description": "Grund, z. B. 'Rabatt', 'Versand'"},
        "reason_code": {"type": "string", "description": "Code: Abschlag 95 = Rabatt; Zuschlag FC = Fracht"},
        "vat_rate": {"type": "number"},
        "vat_category": {"type": "string"},
    },
}

INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": list(FORMATS), "default": "xrechnung-ubl",
                   "description": "xrechnung-ubl/xrechnung-cii für Behörden (XML), zugferd für PDF mit XML"},
        "dry_run": {"type": "boolean", "description": "Nur prüfen und Beträge berechnen, keine Datei erzeugen"},
        "invoice_number": {"type": "string", "description": "Eindeutige, fortlaufende Rechnungsnummer"},
        "issue_date": {"type": "string", "description": "Rechnungsdatum JJJJ-MM-TT (Standard: heute)"},
        "type_code": {"type": "string", "enum": list(TYPE_CODES),
                      "description": "; ".join(f"{k} {v}" for k, v in TYPE_CODES.items())},
        "currency": {"type": "string", "default": "EUR"},
        "delivery_date": {"type": "string", "description": "Liefer-/Leistungsdatum JJJJ-MM-TT"},
        "billing_period": {"type": "object", "description": "Leistungszeitraum",
                           "properties": {"start": {"type": "string"}, "end": {"type": "string"}}},
        "due_date": {"type": "string", "description": "Fälligkeitsdatum JJJJ-MM-TT"},
        "payment_days": {"type": "integer", "description": "Zahlungsziel in Tagen (alternativ zu due_date)"},
        "payment_terms": {"type": "string", "description": "Zahlungsbedingungen als Text"},
        "skonto": {"type": "array", "items": {"type": "object", "properties": {
            "days": {"type": "integer"}, "percent": {"type": "number"}}, "required": ["days", "percent"]}},
        "buyer_reference": {"type": "string", "description": "Käuferreferenz; bei Behörden die Leitweg-ID "
                                                             "(Pflicht bei XRechnung)"},
        "order_reference": {"type": "string", "description": "Bestellnummer des Kunden"},
        "contract_reference": {"type": "string"},
        "project_reference": {"type": "string"},
        "preceding_invoice": {"type": "object", "description": "Ursprüngliche Rechnung bei Gutschrift/Korrektur",
                              "properties": {"number": {"type": "string"}, "date": {"type": "string"}}},
        "seller": _party("Verkäufer – nur angeben, wenn abweichend von den Firmenstammdaten", seller=True),
        "buyer": _party("Käufer / Rechnungsempfänger", seller=False),
        "delivery": {"type": "object", "description": "Abweichende Lieferanschrift",
                     "properties": {"name": {"type": "string"}, "address": _ADDRESS}},
        "lines": {
            "type": "array", "minItems": 1, "description": "Rechnungspositionen",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Bezeichnung der Leistung/Ware"},
                    "description": {"type": "string"},
                    "quantity": {"type": "number", "default": 1},
                    "unit": {"type": "string", "description": "Einheit: Stk, Std, Tag, Monat, kg, m, l, pauschal "
                                                              "oder UN/ECE-Code (H87, HUR, DAY, MON, KGM, LS)"},
                    "unit_price": {"type": "number", "description": "Einzelpreis NETTO"},
                    "price_base_quantity": {"type": "number", "description": "Preis gilt pro dieser Menge (Standard 1)"},
                    "vat_rate": {"type": "number", "description": "USt-Satz in Prozent (Standard 19)"},
                    "vat_category": {"type": "string", "enum": list(VAT_CATEGORIES),
                                     "description": "; ".join(f"{k} {v}" for k, v in VAT_CATEGORIES.items())},
                    "seller_item_id": {"type": "string", "description": "Artikelnummer"},
                    "period_start": {"type": "string"}, "period_end": {"type": "string"},
                    "allowances": {"type": "array", "items": _ALLOWANCE, "description": "Rabatte auf die Position"},
                    "charges": {"type": "array", "items": _ALLOWANCE, "description": "Zuschläge auf die Position"},
                },
                "required": ["name", "unit_price"],
            },
        },
        "allowances": {"type": "array", "items": _ALLOWANCE, "description": "Rabatte auf die gesamte Rechnung"},
        "charges": {"type": "array", "items": _ALLOWANCE, "description": "Zuschläge (z. B. Versand) auf die Rechnung"},
        "prepaid_amount": {"type": "number", "description": "Bereits gezahlter Betrag (Anzahlung)"},
        "vat_exemptions": {"description": "Befreiungsgrund je USt-Kategorie, z. B. {\"E\": \"Steuerfrei gemäß § 4 Nr. 14 UStG\"}",
                           "anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": {"type": "string"}}]},
        "notes": {"type": "array", "items": {"type": "string"}, "description": "Freitexte auf der Rechnung"},
        "payment": {
            "type": "object", "description": "Zahlungsangaben – nur wenn abweichend von den Firmenstammdaten",
            "properties": {
                "means_code": {"type": "string", "description": "58 SEPA-Überweisung, 59 SEPA-Lastschrift, "
                                                                "30 Überweisung, 54 Kreditkarte, 10 bar"},
                "iban": {"type": "string"}, "bic": {"type": "string"}, "account_name": {"type": "string"},
                "remittance_info": {"type": "string", "description": "Verwendungszweck"},
                "mandate_id": {"type": "string"}, "creditor_id": {"type": "string"},
                "debited_iban": {"type": "string"}, "card_number": {"type": "string",
                                                                    "description": "Nur letzte 4–6 Ziffern"},
            },
        },
    },
    "required": ["invoice_number", "buyer", "lines"],
}


def anthropic_tool() -> dict:
    return {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "input_schema": INPUT_SCHEMA}


def openai_tool() -> dict:
    return {"type": "function", "function": {"name": TOOL_NAME, "description": TOOL_DESCRIPTION,
                                             "parameters": INPUT_SCHEMA}}


def mcp_tool() -> dict:
    return {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "inputSchema": INPUT_SCHEMA}


def handle_tool_call(arguments: dict, profile: dict | None = None, output_dir: str | Path | None = None,
                     include_content: bool | None = None) -> dict:
    """Führt den Tool-Aufruf aus.

    output_dir       Dateien dort speichern und Pfade zurückgeben
    include_content  XML/PDF (Base64) im Ergebnis mitliefern; Standard: nur ohne output_dir
    """
    arguments = dict(arguments or {})
    dry_run = bool(arguments.pop("dry_run", False))
    result = create_invoice(arguments, profile=profile, render_pdf=not dry_run)
    out: dict = {"ok": result.ok, "format": result.format, "errors": result.errors, "warnings": result.warnings}
    if result.totals:
        out["totals"] = result.totals.as_dict()
    if not result.ok or dry_run:
        if dry_run and result.ok:
            out["message"] = "Prüfung erfolgreich. Zum Erstellen ohne dry_run erneut aufrufen."
        return out
    out["filename"] = result.filename
    if output_dir:
        out["files"] = [str(p) for p in result.save(output_dir)]
    if include_content if include_content is not None else not output_dir:
        out["xml"] = result.xml.decode("utf-8")
        if result.pdf:
            out["pdf_base64"] = base64.b64encode(result.pdf).decode("ascii")
    return out
