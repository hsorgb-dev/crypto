# E-Rechnung (XRechnung & ZUGFeRD) für das Firmen-KI-Werkzeug

Erzeugt gesetzeskonforme elektronische Rechnungen nach **EN 16931** aus einfachem JSON:

| Format | Ergebnis | Einsatz |
|---|---|---|
| `xrechnung-ubl` (Standard) | XML, XRechnung 3.0 in UBL 2.1 | Öffentliche Auftraggeber (Leitweg-ID), B2B |
| `xrechnung-cii` | XML, XRechnung 3.0 in UN/CEFACT CII | wie oben, falls der Empfänger CII will |
| `zugferd` | PDF/A-3 mit eingebetteter XML (Factur-X/ZUGFeRD, Profil EN 16931) | B2B, Rechnung für Mensch und Maschine |

Eigene Implementierung der offenen Standards, kein Nachbau einer fremden Website.
Der Kern (JSON → XML) braucht **nur die Python-Standardbibliothek** (ab Python 3.10).

## Was das Paket abdeckt

- Rechnung, Gutschrift (381), korrigierte Rechnung (384), Teil-/Abschlags-/Schlussrechnung
- Mehrere Steuersätze, steuerbefreit, Kleinunternehmer (§ 19 UStG), Reverse Charge (§ 13b),
  innergemeinschaftliche Lieferung, Ausfuhr, nicht steuerbar
- Rabatte und Zuschläge je Position und auf die ganze Rechnung, Preis pro Basismenge (z. B. pro 100 Stk),
  Anzahlungen, Skonto im XRechnung-Format, Leistungszeiträume, abweichende Lieferanschrift
- SEPA-Überweisung, SEPA-Lastschrift, Kartenzahlung
- Rechnen nach den Regeln der EN 16931 (kaufmännisch gerundet je USt-Gruppe)
- Vorabprüfung mit **deutschen Fehlermeldungen samt Feldname** (BR-xx, BR-DE-xx, § 14 UStG),
  Prüfziffern von IBAN und Leitweg-ID
- Optional: Prüfung gegen die **offiziellen Regelwerke** (XSD + Schematron EN 16931 1.3.13 und XRechnung 3.0.2)
- Anbindung an KI-Werkzeuge: Tool-Definition (Claude/Anthropic, OpenAI, MCP), MCP-Server, HTTP-API, Kommandozeile

## Schnellstart

```bash
cd e_rechnung
pip install -e ".[pdf,pruefen]"     # nur für PDF und Regelprüfung nötig; der XML-Kern läuft ohne
```

1. `beispiele/firma.json` kopieren und mit Ihren Stammdaten füllen (Name, Anschrift, USt-IdNr./Steuernummer,
   Handelsregister, Ansprechpartner, Bankverbindung, Zahlungsziel). Diese Daten werden jeder Rechnung
   hinterlegt; einzelne Rechnungen müssen sie nicht wiederholen.
2. Rechnung erzeugen:

```bash
python -m erechnung erstellen beispiele/rechnung.json --firma beispiele/firma.json --format xrechnung-ubl -o ausgabe/
```

```bash
python -m erechnung erstellen beispiele/rechnung.json --firma beispiele/firma.json --format zugferd -o ausgabe/
```

3. Optional gegen die offiziellen Regeln prüfen (lädt die Regelwerke einmalig von Maven Central):

```bash
python -m erechnung pruefen ausgabe/RE-2026-0042.xml
```

## Eingabeformat (JSON)

Minimal, wenn die Firmenstammdaten hinterlegt sind:

```json
{
  "invoice_number": "RE-2026-0042",
  "delivery_date": "2026-11-10",
  "buyer_reference": "04011000-1234512345-06",
  "buyer": {
    "name": "Stadtverwaltung Beispielstadt",
    "address": {"street": "Rathausplatz 1", "postal_code": "50667", "city": "Köln"}
  },
  "lines": [
    {"name": "IT-Beratung", "quantity": 12.5, "unit": "Std", "unit_price": 95},
    {"name": "Fachbuch", "quantity": 1, "unit": "Stk", "unit_price": 39.80, "vat_rate": 7}
  ]
}
```

Wichtige Felder (vollständig: `python -m erechnung schema --typ json`):

| Feld | Bedeutung |
|---|---|
| `invoice_number` | Eindeutige, fortlaufende Rechnungsnummer (vergibt Ihr System) |
| `issue_date`, `delivery_date`, `billing_period` | Datum `JJJJ-MM-TT` oder `TT.MM.JJJJ`; Rechnungsdatum Standard heute |
| `type_code` | 380 Rechnung, 381 Gutschrift, 384 Korrektur, 326 Teilrechnung, 875–877 Bau |
| `buyer_reference` | Leitweg-ID (Behörden) bzw. Referenz des Kunden – Pflicht bei XRechnung |
| `lines[].unit_price` | Einzelpreis **netto**; Zahlen auch deutsch (`"349,90"`) |
| `lines[].unit` | `Stk`, `Std`, `Tag`, `Monat`, `kg`, `m`, `l`, `pauschal` … oder UN/ECE-Code |
| `lines[].vat_rate` / `vat_category` | Standard 19 % (S). Kategorien: S, Z, E, AE, K, G, O |
| `allowances`, `charges` | Rabatte/Zuschläge (`amount` oder `percent` + `base_amount`, `reason`) |
| `payment_days`, `due_date`, `skonto` | Zahlungsziel; Skonto als `[{"days": 7, "percent": 2}]` |
| `payment` | `means_code` 58 SEPA-Überweisung, 59 Lastschrift, 54 Karte; IBAN usw. |
| `seller.kleinunternehmer` | `true` → keine USt, Hinweis nach § 19 UStG |
| `vat_exemptions` | Befreiungsgrund je Kategorie, z. B. `{"E": "Steuerfrei gemäß § 4 Nr. 14 UStG"}` |

Weitere Beispiele (Gutschrift, Reverse Charge, Lastschrift, Rabatte …) stehen in `tests/szenarien.py`.

## Anbindung an das KI-Werkzeug

Alle Wege liefern dasselbe Ergebnis-JSON: `ok`, `errors` (was fehlt, mit Feldname), `warnings`,
`totals` (Netto, USt je Satz, Brutto, Zahlbetrag) und die Datei (XML bzw. PDF als Base64 oder Pfad).
Die Tool-Beschreibung weist das Modell an, **fehlende Pflichtangaben beim Nutzer zu erfragen statt sie zu
erfinden** und mit `dry_run: true` die Summen vorab zur Bestätigung zu zeigen.

### 1. MCP (Claude Desktop, LibreChat, n8n, Open WebUI, …)

```json
{
  "mcpServers": {
    "e-rechnung": {
      "command": "python",
      "args": ["-m", "erechnung", "mcp", "--firma", "/pfad/firma.json", "--ausgabe", "/pfad/e-rechnungen"],
      "cwd": "/pfad/zu/e_rechnung"
    }
  }
}
```

### 2. HTTP-API (beliebige Werkzeuge, Copilot Studio, Make, Zapier, eigene Backends)

```bash
export ERECHNUNG_TOKEN=ein-langes-geheimnis
python -m erechnung server --firma firma.json --port 8080
```

```bash
curl -H "Authorization: Bearer $ERECHNUNG_TOKEN" --data @rechnung.json "http://127.0.0.1:8080/rechnung?format=zugferd&download=1" -o rechnung.pdf
```

`POST /rechnung` liefert JSON, mit `download=1` direkt die Datei. `GET /schema?typ=openai|anthropic|mcp`
liefert die Tool-Definition. Der Server lauscht standardmäßig nur auf `127.0.0.1`; für den Betrieb im Netz
einen Reverse-Proxy mit HTTPS davorsetzen.

### 3. Direkt in Python (Function Calling)

```python
import json

import anthropic
from erechnung import load_profile
from erechnung.tool import anthropic_tool, handle_tool_call

client = anthropic.Anthropic()
firma = load_profile("firma.json")
messages = [{"role": "user", "content": "Rechnung an die Stadt Köln: 12,5 Std IT-Beratung à 95 €, Leitweg-ID 04011000-1234512345-06 …"}]

while True:
    response = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        tools=[anthropic_tool()],
        messages=messages,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # lehnt das Modell ab, übernimmt automatisch ein Ersatzmodell
    )
    messages.append({"role": "assistant", "content": response.content})
    if response.stop_reason != "tool_use":
        break
    results = []
    for block in response.content:
        if block.type == "tool_use":
            out = handle_tool_call(block.input, profile=firma, output_dir="e-rechnungen")
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(out, ensure_ascii=False), "is_error": not out["ok"]})
    messages.append({"role": "user", "content": results})

print("".join(b.text for b in response.content if b.type == "text"))
```

Für OpenAI-kompatible Schnittstellen `openai_tool()` statt `anthropic_tool()`
verwenden; der Aufruf `handle_tool_call(...)` bleibt gleich.

## Tests

```bash
cd e_rechnung
python -m pytest -q tests/
```

Die Grundtests laufen ohne Zusatzpakete. Mit `reportlab`, `factur-x`, `lxml` und `saxonche` laufen zusätzlich
die PDF-Tests und die Prüfung aller Beispielrechnungen (12 Szenarien × 3 Formate) gegen XSD und Schematron
von EN 16931 und XRechnung.

## Grenzen – was das Werkzeug nicht übernimmt

- **Rechnungsnummern** vergibt Ihr System (fortlaufend, eindeutig).
- **Versand**: Behörden-Rechnungen gehen über die Portale ZRE/OZG-RE oder Peppol; im B2B per E-Mail o. Ä.
- **Archivierung** nach GoBD (8 Jahre, unveränderbar) ist Sache Ihres Archivsystems.
- Fremdwährungsrechnungen mit USt-Ausweis in EUR (BT-6/BT-111) werden nicht unterstützt.
- Das PDF wird nach PDF/A-3 aufgebaut (eingebettete Schriften, sRGB-Profil, XMP-Metadaten), eine
  Konformitätsprüfung mit veraPDF ist aber nicht Teil der Tests. Rechtlich maßgeblich ist bei ZUGFeRD die
  eingebettete XML, die gegen die offiziellen Regeln geprüft wird.
- Keine Steuerberatung: Steuersätze und Befreiungsgründe liefern Sie; das Werkzeug prüft nur Form und Rechnung.
