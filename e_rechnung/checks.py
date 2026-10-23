"""Business rules of EN 16931 and XRechnung 3.0, checked on the input before any XML is written.

The messages are German and name the input field, so a person or the company AI can fix the
data directly. This is a pre-check: the official proof stays the KoSIT validator
(see validate.py), which the tests run against every generated file."""
import re
from decimal import Decimal

from . import codes
from .model import ZERO, Issue

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def iban_ok(iban):
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', iban):
        return False
    digits = ''.join(str(int(char, 36)) for char in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def check(invoice, profile='xrechnung', receiving=False):
    """All findings for the invoice. profile is 'xrechnung' (adds the BR-DE rules) or 'en16931'.

    receiving=True is for invoices from other software: what the standard allows but this tool
    cannot create (card payment, VAT category L/M, other type codes) is then only a hint."""
    issues = []

    def error(path, message, rule=''):
        issues.append(Issue('fehler', path, message, rule))

    def hint(path, message, rule=''):
        issues.append(Issue('hinweis', path, message, rule))

    def unsupported(path, message, rule=''):
        if receiving:
            hint(path, message + ' Zulässig, kann hier aber nicht erzeugt werden.', rule)
        else:
            error(path, message, rule)

    xrechnung = profile == 'xrechnung'
    seller, buyer, payment = invoice.seller, invoice.buyer, invoice.payment

    if not invoice.number:
        error('number', 'Rechnungsnummer fehlt.', 'BR-02')
    if not invoice.issue_date:
        error('issue_date', 'Rechnungsdatum fehlt.', 'BR-03')
    if invoice.type_code not in codes.TYPES:
        (error if xrechnung else unsupported)(
            'type', f'Rechnungsart {invoice.type_code!r} wird nicht unterstützt. Möglich: '
            + ', '.join(f'{code} ({label})' for code, label in codes.TYPES.items()) + '.',
            'BR-DE-17' if xrechnung else '')
    if not re.fullmatch(r'[A-Z]{3}', invoice.currency):
        error('currency', 'Währung als dreistelliger ISO-Code angeben, z. B. EUR.', 'BR-05')
    if invoice.due_date and invoice.issue_date and invoice.due_date < invoice.issue_date:
        hint('due_date', 'Das Fälligkeitsdatum liegt vor dem Rechnungsdatum.')
    if invoice.period_start and invoice.period_end and invoice.period_end < invoice.period_start:
        error('period.end', 'Das Ende des Leistungszeitraums liegt vor dem Beginn.', 'BR-29')
    if invoice.type_code in ('384', '381') and not invoice.preceding_number:
        hint('preceding_invoice.number', 'Bei einer Korrektur sollte die Nummer der ursprünglichen '
             'Rechnung angegeben werden.', 'BR-DE-26')
    if xrechnung and not invoice.buyer_reference:
        error('buyer_reference', 'Käuferreferenz fehlt. Bei öffentlichen Auftraggebern ist das die '
              'Leitweg-ID, sonst z. B. die Bestell- oder Kundennummer des Käufers.', 'BR-DE-15')
    if not invoice.delivery_date and not (invoice.period_start and invoice.period_end):
        hint('delivery_date', 'Liefer- oder Leistungsdatum fehlt (Pflichtangabe nach § 14 UStG). '
             'delivery_date oder period angeben.')

    for path, party, role in (('seller', seller, 'Verkäufers'), ('buyer', buyer, 'Käufers')):
        address = party.address
        if not party.name:
            error(f'{path}.name', f'Name des {role} fehlt.', 'BR-06' if path == 'seller' else 'BR-07')
        if not re.fullmatch(r'[A-Z]{2}', address.country):
            error(f'{path}.country', 'Land als zweistelligen ISO-Code angeben, z. B. DE.',
                  'BR-09' if path == 'seller' else 'BR-11')
        if path == 'seller' or xrechnung:
            if not address.city:
                error(f'{path}.city', f'Ort des {role} fehlt.',
                      ('BR-DE-3' if path == 'seller' else 'BR-DE-8') if xrechnung else '§ 14 UStG')
            if not address.postcode:
                error(f'{path}.postcode', f'Postleitzahl des {role} fehlt.',
                      ('BR-DE-4' if path == 'seller' else 'BR-DE-9') if xrechnung else '§ 14 UStG')
        if path == 'seller' and not address.street:
            hint('seller.street', 'Straße des Verkäufers fehlt (Pflichtangabe nach § 14 UStG).')
        if party.email and not EMAIL.match(party.email):
            error(f'{path}.email', f'E-Mail-Adresse {party.email!r} ist ungültig.')
        if party.contact.email and not EMAIL.match(party.contact.email):
            error(f'{path}.contact.email', f'E-Mail-Adresse {party.contact.email!r} ist ungültig.', 'BR-DE-28')
        if party.vat_id and not re.fullmatch(r'[A-Z]{2}[A-Z0-9+*.]{2,13}', party.vat_id):
            error(f'{path}.vat_id', f'USt-IdNr. {party.vat_id!r} hat kein gültiges Format '
                  '(Ländercode + Nummer, z. B. DE123456789).')

    if xrechnung:
        if not seller.email:
            error('seller.email', 'Elektronische Adresse (E-Mail) des Verkäufers fehlt.', 'BT-34')
        if not buyer.email:
            error('buyer.email', 'Elektronische Adresse (E-Mail) des Käufers fehlt, bei Behörden '
                  'z. B. die Leitweg-ID-Postfachadresse oder die Rechnungs-E-Mail.', 'BT-49')
        if not seller.contact.name:
            error('seller.contact.name', 'Ansprechpartner des Verkäufers fehlt.', 'BR-DE-5')
        if not seller.contact.phone:
            error('seller.contact.phone', 'Telefonnummer des Ansprechpartners fehlt.', 'BR-DE-6')
        elif len(re.findall(r'\d', seller.contact.phone)) < 3:
            hint('seller.contact.phone', 'Telefonnummer sollte mindestens drei Ziffern haben.', 'BR-DE-27')
        if not seller.contact.email:
            error('seller.contact.email', 'E-Mail des Ansprechpartners fehlt.', 'BR-DE-7')

    if not (seller.vat_id or seller.register_id or seller.id):
        error('seller.vat_id', 'USt-IdNr., Registernummer (z. B. HRB) oder Verkäuferkennung angeben, '
              'damit der Käufer den Verkäufer eindeutig erkennt.', 'BR-CO-26')

    if not invoice.lines:
        error('lines', 'Die Rechnung braucht mindestens eine Position.', 'BR-16')
    ids = set()
    for index, line in enumerate(invoice.lines):
        path = f'lines[{index}]'
        if not line.name:
            error(f'{path}.name', 'Bezeichnung der Position fehlt.', 'BR-25')
        if line.id in ids:
            error(f'{path}.id', f'Positionsnummer {line.id!r} ist doppelt.')
        ids.add(line.id)
        if line.base_quantity <= 0:
            error(f'{path}.price_basis_quantity', 'Preisbasismenge muss größer 0 sein.')
        if line.price < 0:
            error(f'{path}.price', 'Der Nettopreis darf nicht negativ sein, für Gutschriften eine negative '
                  'Menge verwenden.', 'BR-27')
        if not re.fullmatch(r'[A-Z0-9]{1,3}', line.unit):
            error(f'{path}.unit', f'Einheit {line.unit!r} ist kein UN/ECE-Code. Üblich: '
                  + ', '.join(f'{code} ({label})' for code, label in list(codes.UNITS.items())[:9]) + '.')
        if line.discount_amount < 0:
            error(f'{path}.discount', 'Rabatt darf nicht negativ sein.')
        if line.discount_amount and line.gross > 0 and line.discount_amount > line.gross:
            error(f'{path}.discount', 'Rabatt ist höher als der Positionsbetrag.')
        if line.charge_amount < 0:
            error(f'{path}.surcharge', 'Zuschlag darf nicht negativ sein.')

    for key, items in (('allowances', invoice.allowances), ('charges', invoice.charges)):
        for index, item in enumerate(items):
            if item.amount < 0:
                error(f'{key}[{index}].amount', 'Betrag darf nicht negativ sein.')

    categories = {group.category for group in invoice.vat_groups}
    for group in invoice.vat_groups:
        label = f'USt-Kategorie {group.category}'
        if group.category in ('L', 'M'):
            unsupported('lines', f'{label} ({codes.VAT_CATEGORY_LABELS[group.category]}).')
            continue
        if group.category == 'S' and (group.rate is None or group.rate <= 0):
            error('lines', f'{label} braucht einen Steuersatz größer 0.', 'BR-S-05')
        if group.category not in ('S', 'O') and group.rate not in (None, ZERO):
            error('lines', f'{label} muss den Steuersatz 0 haben.', f'BR-{group.category}-05')
        if group.category not in ('S', 'Z') and not group.exemption_reason:
            error('vat_exemption_reason', f'{label} braucht einen Befreiungsgrund, z. B. '
                  f'„Steuerfrei nach § 4 Nr. … UStG“ oder bei Kleinunternehmern seller.small_business = true.',
                  f'BR-{group.category}-10')
    vat_needed = categories & {'S', 'Z', 'E', 'AE', 'K', 'G'}
    if vat_needed and not (seller.vat_id or seller.tax_number):
        error('seller.vat_id', 'USt-IdNr. oder Steuernummer des Verkäufers fehlt.', 'BR-DE-16')
    if 'AE' in categories and not (buyer.vat_id or buyer.register_id):
        error('buyer.vat_id', 'Bei Reverse Charge USt-IdNr. oder Registernummer des Käufers angeben.',
              'BR-AE-02')
    if 'K' in categories and not (seller.vat_id and buyer.vat_id):
        error('buyer.vat_id' if seller.vat_id else 'seller.vat_id', 'Bei innergemeinschaftlicher '
              'Lieferung sind die USt-IdNr. von Verkäufer und Käufer Pflicht.', 'BR-IC-02')
    if 'G' in categories and not seller.vat_id:
        error('seller.vat_id', 'Bei Ausfuhrlieferungen ist die USt-IdNr. des Verkäufers Pflicht.', 'BR-G-02')
    if 'K' in categories:
        if not (invoice.delivery_date or invoice.period_start):
            error('delivery_date', 'Bei innergemeinschaftlicher Lieferung Liefer- oder Leistungsdatum '
                  'angeben.', 'BR-IC-11')
        if (invoice.delivery_address or buyer.address).country == seller.address.country:
            hint('buyer.country', 'Innergemeinschaftliche Lieferung, aber Lieferland gleich Verkäuferland.',
                 'BR-IC-12')
    if 'O' in categories:
        if len(categories) > 1:
            error('lines', 'Nicht steuerbare Positionen (O) dürfen nicht mit anderen USt-Kategorien '
                  'gemischt werden.', 'BR-O-11')
        if seller.vat_id or buyer.vat_id:
            error('seller.vat_id', 'Bei nicht steuerbaren Leistungen (O) keine USt-IdNr. angeben.', 'BR-O-02')

    _check_payment(invoice, error, unsupported)
    if (invoice.due_amount > 0 and not invoice.is_credit_note and not invoice.due_date and not payment.terms
            and not payment.terms_days):
        hint('payment.terms_days', 'Kein Zahlungsziel angegeben, die Rechnung gilt als sofort fällig.')
    if payment.discount_percent is not None and not payment.discount_days:
        error('payment.discount.days', 'Skonto: Anzahl Tage fehlt.', 'BR-DE-18')
    if payment.discount_days and payment.discount_percent is None:
        error('payment.discount.percent', 'Skonto: Prozentsatz fehlt.', 'BR-DE-18')
    if invoice.prepaid and invoice.prepaid > invoice.grand_total and invoice.grand_total > 0:
        hint('prepaid', 'Die Anzahlung ist höher als der Rechnungsbetrag.')
    return issues


def _check_payment(invoice, error, unsupported):
    payment = invoice.payment
    if payment.means not in codes.PAYMENT_MEANS:
        unsupported('payment.means', f'Zahlungsart {payment.means!r} wird nicht unterstützt. Möglich: '
                    + ', '.join(f'{code} ({label})' for code, label in codes.PAYMENT_MEANS.items()) + '.')
        return
    if payment.means in codes.CREDIT_TRANSFER:
        if not payment.iban:
            error('payment.iban', 'IBAN für die Überweisung fehlt.', 'BR-61')
        elif payment.means == '58' and not iban_ok(payment.iban):
            error('payment.iban', f'IBAN {payment.iban!r} ist ungültig (Prüfziffer).', 'BR-DE-19')
    if payment.means in codes.DIRECT_DEBIT:
        if not payment.mandate_id:
            error('payment.mandate_id', 'Mandatsreferenz für die Lastschrift fehlt.')
        if not payment.creditor_id:
            error('payment.creditor_id', 'Gläubiger-ID für die Lastschrift fehlt.')
        if not payment.debtor_iban:
            error('payment.debtor_iban', 'IBAN des Käufers für die Lastschrift fehlt.')
        elif not iban_ok(payment.debtor_iban):
            error('payment.debtor_iban', f'IBAN {payment.debtor_iban!r} ist ungültig (Prüfziffer).', 'BR-DE-20')
    if payment.bic and not re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?', payment.bic):
        error('payment.bic', f'BIC {payment.bic!r} ist ungültig.')
    if payment.discount_percent is not None and not Decimal('0') < payment.discount_percent < Decimal('100'):
        error('payment.discount.percent', 'Skonto-Prozentsatz muss zwischen 0 und 100 liegen.')


def errors(issues):
    return [issue for issue in issues if issue.level == 'fehler']
