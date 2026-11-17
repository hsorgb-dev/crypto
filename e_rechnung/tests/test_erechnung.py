"""Tests für das Paket erechnung.

Grundtests laufen ohne Zusatzpakete. PDF-Tests brauchen reportlab + factur-x,
die Prüfung gegen die offiziellen Regeln lxml + saxonche und Netz (einmaliger
Download der Regelwerke) – fehlt etwas, werden diese Tests übersprungen.
"""
import copy
import importlib.util
import io
import json
import sys
import threading
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from erechnung import create_invoice, iban_valid, leitweg_id_valid, parse_invoice  # noqa: E402
from erechnung.model import InputError  # noqa: E402
from erechnung.tool import anthropic_tool, handle_tool_call, openai_tool  # noqa: E402
from szenarien import BASIS, FIRMA, SZENARIEN  # noqa: E402

FORMATS = ("xrechnung-ubl", "xrechnung-cii", "zugferd")
HAS_PDF = all(importlib.util.find_spec(m) for m in ("reportlab", "facturx", "pypdf", "PIL"))
HAS_VALIDATOR = all(importlib.util.find_spec(m) for m in ("lxml", "saxonche"))
UBL = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
       "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}
CII = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
       "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"}


def make(name="standard", fmt="xrechnung-ubl", **kw):
    return create_invoice(SZENARIEN[name], fmt, FIRMA, render_pdf=False, **kw)


# --- Prüfziffern ------------------------------------------------------------

def test_iban_pruefziffer():
    assert iban_valid("DE02120300000000202051")
    assert iban_valid("DE02 1203 0000 0000 2020 51")
    assert not iban_valid("DE02120300000000202052")
    assert not iban_valid("KEINE-IBAN")


def test_leitweg_id_pruefziffer():
    assert leitweg_id_valid("04011000-1234512345-06")
    assert not leitweg_id_valid("04011000-1234512345-07")
    assert not leitweg_id_valid("BEST-4711")


# --- Einlesen ---------------------------------------------------------------

def test_deutsche_zahlen_und_daten():
    data = copy.deepcopy(FIRMA) | copy.deepcopy(BASIS)
    data["issue_date"] = "17.11.2026"
    data["lines"] = [{"name": "A", "quantity": "1.234,5", "unit": "kg", "unit_price": "2,50"}]
    inv = parse_invoice(data)
    assert inv.issue_date == date(2026, 11, 17)
    assert inv.lines[0].quantity == Decimal("1234.5")
    assert inv.lines[0].unit_price == Decimal("2.50")
    assert inv.lines[0].unit_code == "KGM"


def test_einheiten_werden_uebersetzt():
    data = copy.deepcopy(FIRMA) | copy.deepcopy(BASIS)
    data["lines"] = [{"name": n, "unit": u, "unit_price": 1} for n, u in
                     (("a", "Std"), ("b", "Stück"), ("c", "pauschal"), ("d", "m²"), ("e", "HUR"))]
    assert [line.unit_code for line in parse_invoice(data).lines] == ["HUR", "H87", "LS", "MTK", "HUR"]


def test_alle_eingabefehler_auf_einmal():
    with pytest.raises(InputError) as exc:
        parse_invoice({"buyer": {"address": {}}, "lines": [{"quantity": "abc"}]})
    text = " | ".join(exc.value.errors)
    for expected in ("seller: Angabe fehlt", "buyer.name", "lines[0].quantity", "lines[0].unit_price",
                     "lines[0].name", "invoice_number"):
        assert expected in text


# --- Beträge ----------------------------------------------------------------

def test_betraege_standard():
    t = make().totals
    assert (t.line_total, t.allowance_total, t.tax_basis_total) == (Decimal("1927.10"), Decimal("50.00"),
                                                                     Decimal("1877.10"))
    assert [(g.rate, g.taxable, g.tax) for g in t.vat_groups] == [
        (Decimal(19), Decimal("1837.30"), Decimal("349.09")), (Decimal(7), Decimal("39.80"), Decimal("2.79"))]
    assert (t.tax_total, t.grand_total, t.due_payable) == (Decimal("351.88"), Decimal("2228.98"), Decimal("2228.98"))


def test_betraege_mit_rabatten_zuschlaegen_und_anzahlung():
    r = make("alles_drin")
    t = r.totals
    # 150 - 10 % = 135,00 | 250 × 1,239 + 15 = 324,75 | 400 × 4,50 / 100 = 18,00 | 19,90 (7 %)
    assert t.line_net == {"10": Decimal("135.00"), "20": Decimal("324.75"), "30": Decimal("18.00"),
                          "40": Decimal("19.90")}
    assert t.allowance_total == Decimal("30.00") and t.charge_total == Decimal("24.90")
    assert t.tax_basis_total == Decimal("492.55")
    assert [g.tax for g in t.vat_groups] == [Decimal("89.80"), Decimal("1.39")]
    assert t.grand_total == Decimal("583.74") and t.due_payable == Decimal("83.74")


def test_zahlungsziel_wird_berechnet():
    inv = make().invoice
    assert inv.due_date == date(2026, 12, 1)
    assert "14 Tagen" in inv.payment_terms


# --- Prüfregeln ---------------------------------------------------------------

def test_xrechnung_pflichtangaben_fehlen():
    data = copy.deepcopy(BASIS)
    data.pop("buyer_reference")
    seller = copy.deepcopy(FIRMA["seller"])
    seller.pop("contact")
    seller.pop("electronic_address")
    r = create_invoice(data, "xrechnung-ubl", FIRMA | {"seller": seller})
    assert not r.ok and r.xml is None
    text = " | ".join(r.errors)
    for rule in ("BR-DE-15", "BR-DE-2", "BT-34"):
        assert rule in text
    # Für ZUGFeRD sind diese Angaben nicht Pflicht.
    assert create_invoice(data, "zugferd", FIRMA | {"seller": seller}, render_pdf=False).ok


def test_falsche_iban_und_ust_satz():
    data = copy.deepcopy(BASIS)
    data["payment"] = {"iban": "DE02120300000000202052"}
    data["lines"] = [{"name": "x", "unit_price": 10, "vat_rate": 16}]
    r = create_invoice(data, "xrechnung-ubl", FIRMA)
    assert any("keine gültige IBAN" in e for e in r.errors)
    assert any("kein deutscher Steuersatz" in w for w in r.warnings)


def test_falsche_leitweg_id_ist_warnung():
    r = create_invoice(BASIS | {"buyer_reference": "04011000-1234512345-07"}, "xrechnung-ubl", FIRMA)
    assert r.ok and any("Prüfziffer der Leitweg-ID" in w for w in r.warnings)


def test_steuerbefreiung_braucht_grund():
    data = BASIS | {"lines": [{"name": "Kurs", "unit_price": 100, "vat_category": "E"}], "allowances": []}
    r = create_invoice(data, "zugferd", FIRMA, render_pdf=False)
    assert any("BR-E-10" in e for e in r.errors)


def test_nicht_steuerbar_ohne_ust_id():
    r = make("nicht_steuerbar")
    assert r.ok and b"DE123456789" not in r.xml


def test_kleinunternehmer():
    r = make("kleinunternehmer", "xrechnung-cii")
    assert r.ok and r.totals.tax_total == 0
    assert "§ 19 UStG" in r.xml.decode()


def test_kartennummer_nur_letzte_ziffern():
    data = BASIS | {"payment": {"means_code": "54", "card_number": "4242424242424242"}}
    r = create_invoice(data, "xrechnung-ubl", FIRMA)
    assert any("BR-51" in e for e in r.errors)


# --- XML ----------------------------------------------------------------------

@pytest.mark.parametrize("name", list(SZENARIEN))
@pytest.mark.parametrize("fmt", FORMATS)
def test_alle_szenarien_erzeugbar(name, fmt):
    r = make(name, fmt)
    assert r.ok, r.errors
    ET.fromstring(r.xml)  # wohlgeformt


def test_ubl_inhalt():
    root = ET.fromstring(make().xml)
    assert root.tag.endswith("}Invoice")
    assert root.findtext("cbc:CustomizationID", namespaces=UBL).endswith("xrechnung_3.0")
    assert root.findtext("cbc:BuyerReference", namespaces=UBL) == "04011000-1234512345-06"
    assert root.findtext("cac:LegalMonetaryTotal/cbc:PayableAmount", namespaces=UBL) == "2228.98"
    assert root.find("cac:AccountingSupplierParty/cac:Party/cbc:EndpointID", UBL).get("schemeID") == "EM"
    note = root.findtext("cac:PaymentTerms/cbc:Note", namespaces=UBL)
    assert "#SKONTO#TAGE=7#PROZENT=2.00#" in note and note.endswith("\n")


def test_gutschrift_ist_creditnote():
    root = ET.fromstring(make("gutschrift").xml)
    assert root.tag.endswith("}CreditNote")
    assert root.findtext("cbc:CreditNoteTypeCode", namespaces=UBL) == "381"
    assert root.find("cac:CreditNoteLine", UBL) is not None


def test_cii_inhalt():
    root = ET.fromstring(make(fmt="zugferd").xml)
    guideline = root.findtext("rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID",
                              namespaces=CII)
    assert guideline == "urn:cen.eu:en16931:2017"
    total = root.find(".//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:DuePayableAmount", CII)
    assert total.text == "2228.98"


# --- Werkzeug-Schnittstellen ----------------------------------------------------

def test_tool_definitionen():
    assert anthropic_tool()["input_schema"]["required"] == ["invoice_number", "buyer", "lines"]
    assert openai_tool()["function"]["name"] == "e_rechnung_erstellen"
    json.dumps(anthropic_tool())  # serialisierbar


def test_tool_dry_run_und_fehler():
    out = handle_tool_call(BASIS | {"dry_run": True}, profile=FIRMA)
    assert out["ok"] and out["totals"]["gross_total"] == "2228.98" and "xml" not in out
    out = handle_tool_call({"invoice_number": "1"}, profile=FIRMA)
    assert not out["ok"] and out["errors"]


def test_tool_speichert_dateien(tmp_path):
    out = handle_tool_call(BASIS, profile=FIRMA, output_dir=tmp_path)
    assert out["ok"] and "xml" not in out
    assert Path(out["files"][0]).read_bytes().startswith(b"<?xml")


def test_mcp_server(tmp_path):
    from erechnung.mcp_server import run
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "e_rechnung_erstellen", "arguments": BASIS}}]
    stdout = io.StringIO()
    run(FIRMA, tmp_path, stdin=io.StringIO("\n".join(json.dumps(m) for m in msgs)), stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[1]["result"]["tools"][0]["name"] == "e_rechnung_erstellen"
    result = json.loads(replies[2]["result"]["content"][0]["text"])
    assert result["ok"] and Path(result["files"][0]).exists()


def test_http_api(monkeypatch):
    from http.server import ThreadingHTTPServer

    from erechnung.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FIRMA, "geheim"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/rechnung?format=xrechnung-cii"
    try:
        req = urllib.request.Request(url, data=json.dumps(BASIS).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 401
        req.add_header("Authorization", "Bearer geheim")
        with urllib.request.urlopen(req) as resp:
            out = json.loads(resp.read())
        assert out["ok"] and out["xml"].startswith("<?xml")
    finally:
        httpd.shutdown()


# --- ZUGFeRD-PDF ----------------------------------------------------------------

@pytest.mark.skipif(not HAS_PDF, reason="reportlab/factur-x nicht installiert")
@pytest.mark.parametrize("name", ["standard", "alles_drin", "gutschrift"])
def test_zugferd_pdf(name):
    from facturx import get_xml_from_pdf, xml_check_xsd
    from pypdf import PdfReader
    r = create_invoice(SZENARIEN[name], "zugferd", FIRMA)
    assert r.ok and r.pdf.startswith(b"%PDF-1.7")
    filename, xml = get_xml_from_pdf(r.pdf, check_xsd=False)
    assert filename == "factur-x.xml" and xml == r.xml
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931")  # Factur-X-Schema EN 16931
    reader = PdfReader(io.BytesIO(r.pdf))
    assert "/OutputIntents" in reader.trailer["/Root"]
    fonts = {f.get_object()["/BaseFont"] for p in reader.pages for f in p["/Resources"]["/Font"].values()}
    assert fonts and all("+" in f for f in fonts), fonts  # nur eingebettete (Teil-)Schriften


# --- Offizielle Regeln (XSD + Schematron) -----------------------------------------

@pytest.mark.skipif(not HAS_VALIDATOR, reason="lxml/saxonche nicht installiert")
@pytest.mark.parametrize("name", list(SZENARIEN))
@pytest.mark.parametrize("fmt", FORMATS)
def test_offizielle_regeln(name, fmt):
    from erechnung.validator import validate
    r = make(name, fmt)
    try:
        report = validate(r.xml)
    except (OSError, RuntimeError) as exc:  # Regelwerke nicht ladbar (offline)
        pytest.skip(f"Regelwerke nicht verfügbar: {exc}")
    assert report.ok, report.errors
