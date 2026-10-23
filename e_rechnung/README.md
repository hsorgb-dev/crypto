# E-Rechnung

Erstellt, prüft und zeigt E-Rechnungen nach den deutschen und europäischen Standards an. Das Werkzeug läuft im Browser, auf der Kommandozeile und als Werkzeug für die Firmen-KI (MCP, HTTP-API, Function Calling).

| Format | Datei | Verwendung |
|---|---|---|
| `xrechnung-cii` | XML | XRechnung 3.0, UN/CEFACT CII (Standard, öffentliche Auftraggeber und B2B) |
| `xrechnung-ubl` | XML | XRechnung 3.0, UBL 2.1 (Typ 381 wird als UBL-CreditNote geschrieben) |
| `zugferd` | PDF | ZUGFeRD 2.3 / Factur-X, Profil EN 16931: PDF/A-3b mit eingebetteter `factur-x.xml` |
| `zugferd-xrechnung` | PDF | ZUGFeRD 2.3, Profil XRECHNUNG: PDF/A-3b mit eingebetteter `xrechnung.xml` |
| `pdf` | PDF | nur Sichtbeleg, **keine** E-Rechnung (Vorschau) |

**Funktionen**

- **Erstellen:** Rechnungsdaten als JSON oder über das Formular eingeben. Positionsbeträge, Umsatzsteuer je Steuersatz und alle Summen werden nach EN 16931 berechnet (Rundung kaufmännisch auf Cent).
- **Prüfen vor dem Erstellen:** Pflichtangaben nach EN 16931 und XRechnung (BR-…, BR-DE-…) werden mit deutscher Meldung und Feldname gemeldet, z. B. `buyer_reference: Käuferreferenz fehlt … [BR-DE-15]`.
- **Empfangen:** XRechnung-XML (CII oder UBL) oder ZUGFeRD-/Factur-X-PDF als lesbare Seite anzeigen. Dabei werden die Pflichtangaben geprüft und alle Beträge nachgerechnet.
- **Umwandeln:** zum Beispiel XRechnung-XML in ein ZUGFeRD-PDF oder UBL in CII.
- **Offizielle Prüfung (optional):** Der Mustang-Validator mit den KoSIT-Regeln (EN 16931, XRechnung 3.0) und veraPDF (PDF/A-3) braucht Java.

Unterstützt werden die Rechnungsarten 380, 381, 384, 389, 326, 875, 876 und 877, die USt-Kategorien S, Z, E, AE, K, G und O sowie Kleinunternehmer nach § 19 UStG. Dazu kommen Rabatte und Zuschläge je Position, Zu- und Abschläge auf die ganze Rechnung, Anzahlungen, Skonto (BR-DE-18), Preisbasismengen, Lieferanschrift, Leistungszeitraum, Bestell-, Vertrags- und Projektbezug, SEPA-Überweisung, SEPA-Lastschrift und Barzahlung.

## Installation

```bash
pip install -r e_rechnung/requirements.txt   # reportlab, pypdf, pillow: nur für PDF nötig
```

Für die XML-Formate reicht Python 3.10 oder neuer ohne weitere Pakete. Für PDFs wird eine TrueType-Schrift gesucht, in dieser Reihenfolge: Liberation Sans, DejaVu Sans, Arial und zuletzt die Vera-Schrift aus reportlab. Mit `E_RECHNUNG_FONT=/pfad/schrift.ttf` lässt sich eine eigene Schrift setzen.

## Weboberfläche

```bash
python -m e_rechnung server          # http://127.0.0.1:8765
```

Die Oberfläche hat drei Reiter: **Erstellen** (mit Live-Summen und Prüfung, Fehler werden am Feld markiert), **Empfangen & prüfen** (Datei hineinziehen, anzeigen, umwandeln, ins Formular übernehmen, offiziell prüfen) und **Für die KI / API**. Die Verkäuferdaten lassen sich als Vorlage im Browser speichern. Der Server lauscht standardmäßig nur auf dem eigenen Rechner. Mit `--host 0.0.0.0` ist er im Netz erreichbar, dann aber ohne Anmeldung.

## Kommandozeile

```bash
python -m e_rechnung erstellen e_rechnung/beispiele/rechnung.json -f zugferd -o rechnungen/
python -m e_rechnung pruefen rechnung.json            # Pflichtangaben und Summen
python -m e_rechnung pruefen eingang.pdf --offiziell  # zusätzlich KoSIT/veraPDF
python -m e_rechnung anzeigen eingang.xml             # -> eingang.html
python -m e_rechnung umwandeln eingang.xml -f zugferd
python -m e_rechnung lesen eingang.pdf                # -> JSON im Eingabeformat
python -m e_rechnung validator-laden                  # einmalig, ca. 60 MB, prüft SHA-1
```

## Anbindung an die Firmen-KI

Die KI erzeugt die Rechnungsdaten als JSON (Schema: [`schema.json`](schema.json)) und ruft eines der Werkzeuge auf. Fehlen Pflichtangaben, bekommt sie eine Liste mit Feldnamen und deutscher Erklärung zurück, kann die Daten ergänzen und erneut aufrufen.

| Werkzeug | Zweck |
|---|---|
| `e_rechnung_erstellen` | Rechnungsdaten und Format → Datei, Summen, Hinweise |
| `e_rechnung_pruefen` | Rechnungsdaten → Fehlerliste und Summen, ohne Datei |
| `e_rechnung_lesen` | Pfad einer XML- oder PDF-Datei → Daten und Prüfergebnis |

**MCP** (Claude Desktop, Claude Code, andere MCP-Clients). Die Dateien landen in `E_RECHNUNG_AUSGABE` (Standard: `./rechnungen`):

```json
{"mcpServers": {"e-rechnung": {"command": "python", "args": ["-m", "e_rechnung", "mcp"],
                               "cwd": "/pfad/zum/repository"}}}
```

**HTTP-API** (wenn der Server läuft):

```bash
curl -X POST "http://127.0.0.1:8765/api/erstellen?format=zugferd" \
     -H "Content-Type: application/json" --data @rechnung.json -o rechnung.pdf
curl -X POST "http://127.0.0.1:8765/api/pruefen?format=xrechnung-cii" --data @rechnung.json
curl -X POST "http://127.0.0.1:8765/api/lesen" --data-binary @eingang.pdf
```

Weitere Endpunkte sind `/api/anzeigen`, `/api/umwandeln`, `/api/offiziell`, `/api/schema`, `/api/tools` und `/api/beispiel`.

**Function Calling** in eigener Software. Die Werkzeugbeschreibungen hängt man an die Anfrage an das Sprachmodell. Will das Modell ein Werkzeug benutzen, führt man es so aus:

```python
from e_rechnung.schema import tool_definitions   # name, description, input_schema
from e_rechnung.mcp_server import call_tool

result, is_error = call_tool('e_rechnung_erstellen', {'invoice': rechnungsdaten, 'format': 'zugferd'})
```

**Direkt in Python:**

```python
from e_rechnung import create, InvoiceError
try:
    result = create(daten, 'xrechnung-cii')
    open(result.filename, 'wb').write(result.content)
except InvoiceError as fehler:
    for issue in fehler.issues:
        print(issue)          # FEHLER seller.vat_id: … [BR-CO-26]
```

## Eingabeformat (Kurzfassung)

Ein vollständiges Beispiel steht in [`beispiele/rechnung.json`](beispiele/rechnung.json), weitere Fälle (Gutschrift, Kleinunternehmer, Reverse Charge, innergemeinschaftliche Lieferung, Ausfuhr, Lastschrift, lange Rechnung mit Anzahlung) liegen in [`../tests/e_rechnung_faelle/`](../tests/e_rechnung_faelle/).

```json
{
  "number": "RE-2026-0042", "issue_date": "2026-10-23", "delivery_date": "2026-10-20",
  "buyer_reference": "04011000-12345-67",
  "seller": {"name": "…", "street": "…", "postcode": "…", "city": "…", "vat_id": "DE…",
             "email": "rechnung@…", "contact": {"name": "…", "phone": "…", "email": "…"}},
  "buyer":  {"name": "…", "street": "…", "postcode": "…", "city": "…", "email": "…"},
  "lines":  [{"name": "Beratung", "quantity": 8, "unit": "HUR", "price": 120, "vat_rate": 19}],
  "payment": {"iban": "DE…", "terms_days": 30, "discount": {"days": 10, "percent": 2}}
}
```

Zahlen dürfen auch deutsch geschrieben sein (`"1.234,56"`), Datumsangaben als `JJJJ-MM-TT` oder `TT.MM.JJJJ`. Einheiten können als UN/ECE-Code oder deutsches Kürzel angegeben werden (`Stk`, `Std`, `Tag`, `pauschal`, `m²` …).

## Was nicht abgedeckt ist

- Versand über Peppol oder Behördenportale (ZRE/OZG-RE). Das Werkzeug erzeugt nur die Datei.
- ZUGFeRD-Profile MINIMUM, BASIC WL und EXTENDED, ZUGFeRD 1.0, Rechnungsanhänge (BG-24), Kartenzahlung, mehrere Zahlungswege in einer Rechnung, USt-Kategorien L und M.
- Beim Umwandeln fremder Rechnungen gehen Felder verloren, die das Eingabeformat nicht kennt (z. B. Anhänge). Für die Anzeige und die Prüfung spielt das keine Rolle.
- Die eingebaute Prüfung ist eine inhaltliche Vorprüfung. Verbindlich ist der KoSIT-Validator. Die Tests prüfen jede erzeugte Datei damit, siehe unten. Das Werkzeug ist keine Steuerberatung.

## Tests

```bash
python -m pytest -q tests/test_e_rechnung.py
# zusätzlich die offizielle Prüfung aller Testfälle (Java nötig, dauert einige Minuten):
python -m e_rechnung validator-laden
E_RECHNUNG_OFFIZIELL=1 python -m pytest -q tests/test_e_rechnung.py
```
