"""JSON schema of the invoice input, and the tool definitions for the company AI (function calling / MCP).

`python -m e_rechnung schema` prints the schema; schema.json in this folder is the same content."""
from . import codes


def _s(description, **extra):
    return {'type': 'string', 'description': description, **extra}


def _n(description, **extra):
    return {'type': ['number', 'string'], 'description': description, **extra}


ADDRESS = {
    'street': _s('Straße und Hausnummer.'),
    'additional_street': _s('Adresszusatz.'),
    'postcode': _s('Postleitzahl.'),
    'city': _s('Ort.'),
    'country': _s('Land als ISO-Code, z. B. DE, AT, FR. Standard DE.'),
}
CONTACT = {'type': 'object', 'description': 'Ansprechpartner.', 'properties': {
    'name': _s('Name des Ansprechpartners.'), 'phone': _s('Telefonnummer.'), 'email': _s('E-Mail-Adresse.')}}

SELLER = {
    'type': 'object',
    'description': 'Verkäufer, also das eigene Unternehmen, das die Rechnung stellt.',
    'required': ['name', 'postcode', 'city'],
    'properties': {
        'name': _s('Vollständiger Firmenname laut Register.'),
        **ADDRESS,
        'vat_id': _s('USt-IdNr., z. B. DE123456789.'),
        'tax_number': _s('Steuernummer des Finanzamts (wenn keine USt-IdNr. vorhanden ist).'),
        'register_id': _s('Handelsregister, z. B. "HRB 12345, Amtsgericht Berlin".'),
        'legal_info': _s('Weitere rechtliche Angaben, z. B. Geschäftsführer.'),
        'trading_name': _s('Abweichender Handelsname.'),
        'id': _s('Verkäuferkennung, z. B. Lieferantennummer beim Kunden.'),
        'email': _s('Elektronische Adresse des Verkäufers für E-Rechnungen (XRechnung: Pflicht).'),
        'contact': {**CONTACT, 'description': 'Ansprechpartner beim Verkäufer (XRechnung: Name, Telefon und E-Mail Pflicht).'},
        'small_business': {'type': 'boolean', 'description': 'Kleinunternehmer nach § 19 UStG: keine Umsatzsteuer, '
                           'alle Positionen werden steuerbefreit (Kategorie E) mit Hinweis ausgewiesen.'},
    },
}
BUYER = {
    'type': 'object',
    'description': 'Käufer / Rechnungsempfänger.',
    'required': ['name'],
    'properties': {
        'name': _s('Name des Käufers.'),
        **ADDRESS,
        'vat_id': _s('USt-IdNr. des Käufers (Pflicht bei Reverse Charge und innergemeinschaftlicher Lieferung).'),
        'register_id': _s('Registernummer des Käufers.'),
        'id': _s('Kundennummer.'),
        'trading_name': _s('Abweichender Handelsname.'),
        'email': _s('Elektronische Adresse des Käufers für E-Rechnungen (XRechnung: Pflicht).'),
        'contact': CONTACT,
    },
}
VAT = {
    'vat_category': _s('USt-Kategorie: S Normal-/ermäßigter Satz (Standard), Z Nullsatz, E steuerbefreit, '
                       'AE Reverse Charge (§ 13b UStG), K innergemeinschaftliche Lieferung, G Ausfuhr, O nicht steuerbar.',
                       enum=codes.VAT_CATEGORIES),
    'vat_rate': _n('Steuersatz in Prozent, z. B. 19 oder 7. Standard 19 bei Kategorie S, sonst 0.'),
}
LINE = {
    'type': 'object',
    'required': ['name', 'quantity', 'price'],
    'properties': {
        'name': _s('Bezeichnung der Leistung oder des Artikels.'),
        'description': _s('Beschreibung (optional).'),
        'quantity': _n('Menge. Negativ für Gutschrift-/Stornopositionen.'),
        'unit': _s('Einheit als UN/ECE-Code, z. B. ' + ', '.join(f'{c} = {l}' for c, l in codes.UNITS.items())
                   + '. Deutsche Kürzel wie Stk, Std, Tag, pauschal werden umgesetzt. Standard C62.'),
        'price': _n('Einzelpreis netto (ohne Umsatzsteuer) je Einheit.'),
        'price_basis_quantity': _n('Preisbasismenge, z. B. 100 wenn der Preis für 100 Stück gilt. Standard 1.'),
        **VAT,
        'item_id': _s('Artikelnummer des Verkäufers.'),
        'buyer_item_id': _s('Artikelnummer des Käufers.'),
        'order_line': _s('Positionsnummer in der Bestellung des Käufers.'),
        'note': _s('Bemerkung zur Position.'),
        'id': _s('Positionsnummer. Standard: fortlaufend 1, 2, 3 …'),
        'discount': {'type': 'object', 'description': 'Rabatt auf diese Position (percent oder amount).',
                     'properties': {'percent': _n('Rabatt in Prozent.'), 'amount': _n('Rabatt als Betrag netto.'),
                                    'reason': _s('Grund, z. B. "Mengenrabatt". Standard "Rabatt".')}},
        'surcharge': {'type': 'object', 'description': 'Zuschlag auf diese Position.',
                      'properties': {'amount': _n('Zuschlag als Betrag netto.'),
                                     'reason': _s('Grund, z. B. "Expresszuschlag". Standard "Zuschlag".')}},
    },
}
ALLOWANCE_CHARGE = {
    'type': 'object',
    'required': ['reason'],
    'properties': {
        'amount': _n('Betrag netto.'),
        'percent': _n('Alternativ Prozentsatz auf die Positionen mit gleicher USt.'),
        'base': _n('Grundbetrag für percent (optional).'),
        'reason': _s('Grund, z. B. "Versandkosten" oder "Treuerabatt".'),
        **VAT,
    },
}

SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': 'E-Rechnung',
    'description': 'Rechnungsdaten für XRechnung 3.0 und ZUGFeRD 2.3. Beträge netto, Umsatzsteuer und Summen '
                   'werden automatisch berechnet. Datumsangaben im Format JJJJ-MM-TT.',
    'type': 'object',
    'required': ['number', 'issue_date', 'seller', 'buyer', 'lines'],
    'properties': {
        'number': _s('Rechnungsnummer (eindeutig, fortlaufend).'),
        'type': _s('Rechnungsart: ' + ', '.join(f'{c} {l}' for c, l in codes.TYPES.items()) + '. Standard 380.',
                   enum=list(codes.TYPES)),
        'issue_date': _s('Rechnungsdatum, JJJJ-MM-TT.'),
        'delivery_date': _s('Liefer- oder Leistungsdatum, JJJJ-MM-TT.'),
        'period': {'type': 'object', 'description': 'Leistungszeitraum statt Leistungsdatum.',
                   'properties': {'start': _s('Beginn, JJJJ-MM-TT.'), 'end': _s('Ende, JJJJ-MM-TT.')}},
        'due_date': _s('Fälligkeitsdatum, JJJJ-MM-TT. Alternativ payment.terms_days.'),
        'currency': _s('Währung (ISO 4217). Standard EUR.'),
        'buyer_reference': _s('Käuferreferenz. Bei Behörden die Leitweg-ID, sonst z. B. Bestellnummer oder '
                              'Kundennummer. Für XRechnung Pflicht.'),
        'order_reference': _s('Bestellnummer des Käufers.'),
        'contract_reference': _s('Vertragsnummer.'),
        'project_reference': _s('Projektnummer.'),
        'preceding_invoice': {'type': 'object', 'description': 'Bezug auf eine frühere Rechnung (bei Korrekturen).',
                              'properties': {'number': _s('Nummer der früheren Rechnung.'),
                                             'date': _s('Datum der früheren Rechnung.')}},
        'note': {'type': ['string', 'array'], 'items': {'type': 'string'}, 'description': 'Freitext auf der Rechnung.'},
        'seller': SELLER,
        'buyer': BUYER,
        'delivery': {'type': 'object', 'description': 'Abweichende Lieferanschrift.',
                     'properties': {'name': _s('Name am Lieferort.'), **ADDRESS}},
        'lines': {'type': 'array', 'minItems': 1, 'items': LINE, 'description': 'Rechnungspositionen.'},
        'allowances': {'type': 'array', 'items': ALLOWANCE_CHARGE, 'description': 'Nachlässe auf die ganze Rechnung.'},
        'charges': {'type': 'array', 'items': ALLOWANCE_CHARGE,
                    'description': 'Zuschläge auf die ganze Rechnung, z. B. Versand.'},
        'vat_exemption_reason': _s('Befreiungsgrund für steuerfreie Umsätze, z. B. "Steuerfrei nach § 4 Nr. 14 '
                                   'UStG". Für AE, K, G, O und Kleinunternehmer gibt es einen Standardtext.'),
        'payment': {
            'type': 'object',
            'description': 'Zahlungsangaben.',
            'properties': {
                'means': _s('Zahlungsart: ' + ', '.join(f'{c} {l}' for c, l in codes.PAYMENT_MEANS.items())
                            + '. Standard 58.', enum=list(codes.PAYMENT_MEANS)),
                'iban': _s('IBAN des Verkäufers (bei Überweisung Pflicht).'),
                'bic': _s('BIC (optional).'),
                'account_name': _s('Kontoinhaber. Standard: Name des Verkäufers.'),
                'reference': _s('Verwendungszweck. Standard: Rechnungsnummer.'),
                'terms': _s('Zahlungsbedingungen als Text. Ohne Angabe wird "Zahlbar ohne Abzug bis …" erzeugt.'),
                'terms_days': {'type': 'integer', 'description': 'Zahlungsziel in Tagen ab Rechnungsdatum.'},
                'discount': {'type': 'object', 'description': 'Skonto.',
                             'properties': {'days': {'type': 'integer', 'description': 'Tage.'},
                                            'percent': _n('Prozent.')}},
                'mandate_id': _s('SEPA-Mandatsreferenz (bei Lastschrift).'),
                'creditor_id': _s('Gläubiger-ID des Verkäufers (bei Lastschrift).'),
                'debtor_iban': _s('IBAN des Käufers (bei Lastschrift).'),
            },
        },
        'prepaid': _n('Bereits gezahlter Betrag (Anzahlung), wird vom Zahlbetrag abgezogen.'),
    },
}

FORMAT_DESCRIPTION = ('Ausgabeformat: xrechnung-cii (Standard, XML für Behörden und B2B), xrechnung-ubl (XML), '
                      'zugferd (PDF mit eingebetteter XML, Profil EN 16931, für Firmenkunden), '
                      'zugferd-xrechnung (PDF, Profil XRECHNUNG).')


def tool_definitions():
    """Tools in the shape the Claude API (input_schema) and MCP (inputSchema) expect."""
    formats = ['xrechnung-cii', 'xrechnung-ubl', 'zugferd', 'zugferd-xrechnung']
    return [
        {'name': 'e_rechnung_erstellen',
         'description': 'Erstellt eine E-Rechnung (XRechnung oder ZUGFeRD) aus Rechnungsdaten und speichert sie als '
                        'Datei. Summen und Umsatzsteuer werden berechnet. Fehlen Pflichtangaben, kommt eine Liste '
                        'der Fehler mit Feldnamen zurück; dann die Daten ergänzen und erneut aufrufen.',
         'input_schema': {'type': 'object', 'required': ['invoice'], 'properties': {
             'invoice': SCHEMA, 'format': {'type': 'string', 'enum': formats, 'description': FORMAT_DESCRIPTION}}}},
        {'name': 'e_rechnung_pruefen',
         'description': 'Prüft Rechnungsdaten auf Pflichtangaben nach EN 16931 / XRechnung und berechnet die Summen, '
                        'ohne eine Datei zu erzeugen.',
         'input_schema': {'type': 'object', 'required': ['invoice'], 'properties': {
             'invoice': SCHEMA, 'format': {'type': 'string', 'enum': formats, 'description': FORMAT_DESCRIPTION}}}},
        {'name': 'e_rechnung_lesen',
         'description': 'Liest eine empfangene E-Rechnung (XRechnung-XML oder ZUGFeRD-PDF) und gibt die Daten und '
                        'die Prüfergebnisse zurück (Pflichtangaben und Nachrechnung).',
         'input_schema': {'type': 'object', 'required': ['path'], 'properties': {
             'path': {'type': 'string', 'description': 'Pfad der XML- oder PDF-Datei.'}}}},
    ]
