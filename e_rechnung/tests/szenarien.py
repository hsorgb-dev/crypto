"""Testszenarien: decken die wichtigsten Rechnungsarten und Sonderfälle ab."""
import copy
import json
from pathlib import Path

BEISPIELE = Path(__file__).resolve().parents[1] / "beispiele"
FIRMA = json.loads((BEISPIELE / "firma.json").read_text(encoding="utf-8"))
BASIS = json.loads((BEISPIELE / "rechnung.json").read_text(encoding="utf-8"))

EU_KUNDE = {
    "name": "Alpen Handel GmbH",
    "address": {"street": "Ringstraße 5", "postal_code": "1010", "city": "Wien", "country_code": "AT"},
    "vat_id": "ATU12345678",
    "electronic_address": "invoice@alpen-handel.example",
}


def _mit(**changes):
    data = copy.deepcopy(BASIS)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


SZENARIEN = {
    "standard": _mit(),
    "gutschrift": _mit(type_code="381", invoice_number="GS-2026-0007",
                       preceding_invoice={"number": "RE-2026-0042", "date": "2026-11-17"},
                       project_reference="PRJ-17", skonto=None,
                       lines=[{"name": "Gutschrift Switch (defekt)", "quantity": 1, "unit": "Stk", "unit_price": 349.90}],
                       allowances=None),
    "korrektur": _mit(type_code="384", preceding_invoice={"number": "RE-2026-0041"}),
    "reverse_charge": _mit(buyer=EU_KUNDE, buyer_reference="PO-AT-1", allowances=None,
                           lines=[{"name": "Wartungsvertrag Q4", "quantity": 1, "unit": "LS", "unit_price": 1200,
                                   "vat_category": "AE"}]),
    "innergemeinschaftlich": _mit(buyer=EU_KUNDE, buyer_reference="PO-AT-2", allowances=None,
                                  lines=[{"name": "Netzwerk-Switch 24 Port", "quantity": 10, "unit": "Stk",
                                          "unit_price": 349.90, "vat_category": "K"}]),
    "ausfuhr": _mit(buyer={"name": "Swiss Tools AG", "address": {"street": "Bahnhofstr. 1", "postal_code": "8001",
                                                                  "city": "Zürich", "country_code": "CH"},
                           "electronic_address": "ap@swisstools.example"},
                    buyer_reference="CH-77", allowances=None,
                    lines=[{"name": "Messgerät", "quantity": 3, "unit": "Stk", "unit_price": 899, "vat_category": "G"}]),
    "nicht_steuerbar": _mit(allowances=None, lines=[{"name": "Schadenersatz", "quantity": 1, "unit": "LS",
                                                     "unit_price": 500, "vat_category": "O"}]),
    "steuerbefreit": _mit(allowances=None, vat_exemptions={"E": "Steuerfrei gemäß § 4 Nr. 21 UStG"},
                          lines=[{"name": "Schulung Netzwerksicherheit", "quantity": 2, "unit": "Tag",
                                  "unit_price": 800, "vat_category": "E"}]),
    "kleinunternehmer": _mit(allowances=None,
                             seller={"kleinunternehmer": True, "vat_id": None, "id": "KU-4711"},
                             lines=[{"name": "Webdesign", "quantity": 8, "unit": "Std", "unit_price": 60}]),
    "lastschrift": _mit(payment={"means_code": "59", "mandate_id": "MANDAT-0815", "creditor_id": "DE98ZZZ09999999999",
                                 "debited_iban": "DE75512108001245126199", "iban": None, "bic": None}),
    "kreditkarte": _mit(payment={"means_code": "54", "card_number": "4242", "card_holder": "Max Mustermann",
                                 "iban": None, "bic": None}),
    "alles_drin": _mit(
        billing_period={"start": "2026-10-01", "end": "2026-10-31"},
        delivery_date=None,
        contract_reference="RV-2024-11", project_reference="PRJ-NETZ", prepaid_amount=500,
        delivery={"name": "Außenstelle Nord", "address": {"street": "Hafenweg 3", "postal_code": "20457",
                                                          "city": "Hamburg", "country_code": "DE"}},
        notes=["Vielen Dank!", {"text": "Es gelten unsere AGB.", "subject_code": "AAI"}],
        charges=[{"amount": 24.90, "reason": "Versand", "reason_code": "FC"}],
        allowances=[{"percent": 3, "base_amount": 1000, "reason": "Projektrabatt", "reason_code": "95"}],
        lines=[
            {"id": "10", "name": "Wartung", "quantity": 1, "unit": "Monat", "unit_price": 150,
             "period_start": "2026-10-01", "period_end": "2026-10-31", "note": "Oktober",
             "allowances": [{"percent": 10, "reason": "Mengenrabatt", "reason_code": "95"}],
             "order_line_ref": "4", "buyer_item_id": "K-778"},
            {"id": "20", "name": "Kabel Cat7", "quantity": 250, "unit": "m", "unit_price": 1.239,
             "charges": [{"amount": 15, "reason": "Konfektionierung", "reason_code": "ABK"}]},
            {"id": "30", "name": "Schrauben M4", "quantity": 400, "unit": "Stk", "unit_price": 4.50,
             "price_base_quantity": 100},
            {"id": "40", "name": "Handbuch", "quantity": 1, "unit": "Stk", "unit_price": 19.90, "vat_rate": 7},
        ]),
}
