"""Vorabprüfung einer Rechnung gegen die wichtigsten Regeln.

Geprüft werden Geschäftsregeln der EN 16931 (BR-xx), die deutschen
XRechnung-Regeln (BR-DE-xx) und Pflichtangaben nach § 14 UStG. Die Meldungen
sind deutsch und nennen das betroffene Feld, damit ein KI-Werkzeug gezielt
nachfragen kann. Die vollständige Prüfung macht die offizielle Schematron
(siehe README); diese Vorabprüfung fängt die typischen Lücken vorher ab.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .calc import Totals
from .model import TYPE_CODES, VAT_CATEGORIES, Invoice, Party, exemption_for

FORMATS = ("xrechnung-ubl", "xrechnung-cii", "zugferd")
FORMAT_ALIASES = {
    "xrechnung": "xrechnung-ubl", "ubl": "xrechnung-ubl", "xrechnung_ubl": "xrechnung-ubl",
    "cii": "xrechnung-cii", "xrechnung_cii": "xrechnung-cii",
    "zugferd-pdf": "zugferd", "factur-x": "zugferd", "facturx": "zugferd", "pdf": "zugferd",
}

LEITWEG_RE = re.compile(r"^\d{2,12}(-[A-Za-z0-9]{1,30})?-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
CARD_CODES = ("48", "54", "55")
TRANSFER_CODES = ("30", "58")
DIRECT_DEBIT_CODES = ("59",)


def normalize_format(fmt: str | None) -> str:
    key = (fmt or "xrechnung-ubl").strip().lower()
    key = FORMAT_ALIASES.get(key, key)
    if key not in FORMATS:
        raise ValueError(f"Unbekanntes Format '{fmt}'. Möglich: {', '.join(FORMATS)}")
    return key


def _mod97(text: str) -> int:
    digits = "".join(str(int(c, 36)) for c in text.upper())
    return int(digits) % 97


def iban_valid(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    return _mod97(iban[4:] + iban[:4]) == 1


def leitweg_id_valid(value: str) -> bool:
    """Prüfziffer der Leitweg-ID (ISO/IEC 7064, MOD 97-10)."""
    if not LEITWEG_RE.fullmatch(value):
        return False
    return _mod97(value.replace("-", "")) == 1


def guess_scheme(address: str) -> str | None:
    if "@" in address:
        return "EM"
    if LEITWEG_RE.fullmatch(address):
        return "0204"
    return None


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def complete(invoice: Invoice, fmt: str) -> list[str]:
    """Ergänzt ableitbare Angaben in-place und gibt Hinweise dazu zurück."""
    hints = []
    seller, buyer = invoice.seller, invoice.buyer
    if not seller.electronic_address and seller.contact and seller.contact.email:
        seller.electronic_address = seller.contact.email
    if not buyer.electronic_address:
        if invoice.buyer_reference and LEITWEG_RE.fullmatch(invoice.buyer_reference):
            buyer.electronic_address = invoice.buyer_reference
        elif buyer.contact and buyer.contact.email:
            buyer.electronic_address = buyer.contact.email
            if fmt != "zugferd":
                hints.append("buyer.electronic_address: Kontakt-E-Mail des Käufers als Empfangsadresse "
                             "verwendet – bitte prüfen, ob dort E-Rechnungen ankommen sollen.")
    for party in (seller, buyer):
        if party.electronic_address and not party.electronic_address_scheme:
            party.electronic_address_scheme = guess_scheme(party.electronic_address)
    # Nicht steuerbare Umsätze: keine USt-IdNr. in der Rechnung (BR-O-02).
    if any(line.vat_category == "O" for line in invoice.lines):
        for role, party in (("seller", seller), ("buyer", buyer)):
            if party.vat_id:
                party.vat_id = None
                hints.append(f"{role}.vat_id: Bei nicht steuerbaren Umsätzen (O) wird die USt-IdNr. "
                             "nicht übertragen (BR-O-02).")
    # Innergemeinschaftliche Lieferung braucht ein Lieferland (BR-IC-12).
    if any(line.vat_category == "K" for line in invoice.lines) and invoice.delivery_party is None:
        invoice.delivery_party = Party(name=buyer.name, address=buyer.address)
    return hints


def _check_party(res: CheckResult, party: Party, role: str, xrechnung: bool) -> None:
    a = party.address
    if not re.fullmatch(r"[A-Z]{2}", a.country_code or ""):
        res.errors.append(f"{role}.address.country_code: Ländercode mit 2 Buchstaben angeben (z. B. DE)")
    if xrechnung or role == "seller":
        if not a.city:
            res.errors.append(f"{role}.address.city: Ort fehlt (BR-DE-3/BR-DE-8)")
        if not a.postal_code:
            res.errors.append(f"{role}.address.postal_code: Postleitzahl fehlt (BR-DE-4/BR-DE-9)")
    if not a.street:
        res.warnings.append(f"{role}.address.street: Straße fehlt (§ 14 Abs. 4 Nr. 1 UStG verlangt die vollständige Anschrift)")
    if party.vat_id and not re.fullmatch(r"[A-Z]{2}[A-Z0-9+*]{2,13}", party.vat_id):
        res.errors.append(f"{role}.vat_id: '{party.vat_id}' ist keine gültige USt-IdNr. (Ländercode + Nummer, z. B. DE123456789)")
    if party.vat_id and party.vat_id.startswith("DE") and not re.fullmatch(r"DE\d{9}", party.vat_id):
        res.errors.append(f"{role}.vat_id: Deutsche USt-IdNr. hat das Format DE + 9 Ziffern")
    if party.electronic_address:
        if not party.electronic_address_scheme:
            res.errors.append(f"{role}.electronic_address.scheme: Schema der elektronischen Adresse angeben "
                              "(EM = E-Mail, 0204 = Leitweg-ID, 9930 = USt-IdNr. DE, 0088 = GLN)")
        elif party.electronic_address_scheme == "EM" and not EMAIL_RE.fullmatch(party.electronic_address):
            res.errors.append(f"{role}.electronic_address: '{party.electronic_address}' ist keine gültige E-Mail-Adresse")
    elif xrechnung:
        res.errors.append(f"{role}.electronic_address: Elektronische Adresse fehlt (BT-{'34' if role == 'seller' else '49'}, "
                          "Pflicht in XRechnung 3.0), z. B. die E-Mail-Adresse für E-Rechnungen")
    c = party.contact
    if c and c.email and not EMAIL_RE.fullmatch(c.email):
        res.warnings.append(f"{role}.contact.email: '{c.email}' sieht nicht wie eine E-Mail-Adresse aus (BR-DE-28)")
    if c and c.phone and len(re.sub(r"\D", "", c.phone)) < 3:
        res.warnings.append(f"{role}.contact.phone: Telefonnummer mit mindestens 3 Ziffern angeben (BR-DE-27)")


def check(invoice: Invoice, totals: Totals, fmt: str) -> CheckResult:
    res = CheckResult()
    xrechnung = fmt.startswith("xrechnung")
    seller, buyer = invoice.seller, invoice.buyer
    categories = {line.vat_category for line in invoice.lines}
    categories |= {ac.vat_category for ac in invoice.allowances + invoice.charges}

    # Kopf
    if invoice.type_code not in TYPE_CODES:
        res.errors.append(f"type_code: '{invoice.type_code}' nicht erlaubt (BR-DE-17). "
                          f"Möglich: {', '.join(f'{k} = {v}' for k, v in TYPE_CODES.items())}")
    if not re.fullmatch(r"[A-Z]{3}", invoice.currency):
        res.errors.append("currency: Währung als ISO-Code mit 3 Buchstaben angeben (z. B. EUR)")
    elif invoice.currency != "EUR":
        res.warnings.append("currency: Bei Fremdwährung muss die Umsatzsteuer zusätzlich in EUR "
                            "ausgewiesen werden (§ 16 Abs. 6 UStG); das unterstützt dieses Werkzeug nicht.")
    if xrechnung:
        if not invoice.buyer_reference:
            res.errors.append("buyer_reference: Käuferreferenz fehlt (BR-DE-15). Bei öffentlichen "
                              "Auftraggebern die Leitweg-ID, sonst die Referenz/Bestellnummer des Kunden.")
        elif LEITWEG_RE.fullmatch(invoice.buyer_reference) and not leitweg_id_valid(invoice.buyer_reference):
            res.warnings.append(f"buyer_reference: Die Prüfziffer der Leitweg-ID '{invoice.buyer_reference}' "
                                "stimmt nicht – bitte beim Auftraggeber nachfragen.")
    if invoice.type_code in ("381", "384") and not invoice.preceding_invoice_number:
        res.warnings.append("preceding_invoice.number: Bei Gutschrift/Korrektur die ursprüngliche "
                            "Rechnungsnummer angeben (BR-DE-26)")
    if invoice.period_start and invoice.period_end and invoice.period_end < invoice.period_start:
        res.errors.append("billing_period: Ende liegt vor dem Beginn (BR-29)")
    if invoice.due_date and invoice.due_date < invoice.issue_date:
        res.warnings.append("due_date: Fälligkeitsdatum liegt vor dem Rechnungsdatum")
    if not (invoice.delivery_date or invoice.period_start or invoice.period_end
            or any(line.period_start or line.period_end for line in invoice.lines)):
        res.warnings.append("delivery_date: Liefer-/Leistungsdatum oder Leistungszeitraum fehlt "
                            "(Pflichtangabe nach § 14 Abs. 4 Nr. 6 UStG)")

    # Parteien
    _check_party(res, seller, "seller", xrechnung)
    _check_party(res, buyer, "buyer", xrechnung)
    if invoice.delivery_party:
        if xrechnung and not invoice.delivery_party.address.city:
            res.errors.append("delivery.address.city: Ort der Lieferanschrift fehlt (BR-DE-10)")
        if xrechnung and not invoice.delivery_party.address.postal_code:
            res.errors.append("delivery.address.postal_code: PLZ der Lieferanschrift fehlt (BR-DE-11)")
    if not (seller.id or seller.registration_id or seller.vat_id):
        res.errors.append("seller: USt-IdNr. (vat_id), Handelsregisternummer (registration_id) oder eine "
                          "Verkäuferkennung (id) angeben (BR-CO-26)")
    if xrechnung:
        c = seller.contact
        if not c:
            res.errors.append("seller.contact: Ansprechpartner mit Name, Telefon und E-Mail fehlt (BR-DE-2)")
        else:
            if not c.name:
                res.errors.append("seller.contact.name: Name des Ansprechpartners fehlt (BR-DE-5)")
            if not c.phone:
                res.errors.append("seller.contact.phone: Telefonnummer fehlt (BR-DE-6)")
            if not c.email:
                res.errors.append("seller.contact.email: E-Mail-Adresse fehlt (BR-DE-7)")
    if (categories & {"S", "Z", "E", "AE", "K", "G", "L", "M"}) and not (seller.vat_id or seller.tax_number):
        res.errors.append("seller: Steuernummer (tax_number) oder USt-IdNr. (vat_id) fehlt "
                          "(§ 14 Abs. 4 Nr. 2 UStG, BR-S-2/BR-DE-16)")
    if "AE" in categories:
        if not (buyer.vat_id or buyer.registration_id):
            res.errors.append("buyer.vat_id: Bei Reverse Charge (AE) die USt-IdNr. des Käufers angeben (BR-AE-2)")
    if "K" in categories:
        if not seller.vat_id:
            res.errors.append("seller.vat_id: Bei innergemeinschaftlicher Lieferung (K) ist die USt-IdNr. Pflicht (BR-IC-2)")
        if not buyer.vat_id:
            res.errors.append("buyer.vat_id: Bei innergemeinschaftlicher Lieferung (K) ist die USt-IdNr. des Käufers Pflicht (BR-IC-2)")
        if not (invoice.delivery_date or invoice.period_start):
            res.errors.append("delivery_date: Bei innergemeinschaftlicher Lieferung (K) Lieferdatum oder "
                              "Leistungszeitraum angeben (BR-IC-11)")
    if "G" in categories and not seller.vat_id:
        res.errors.append("seller.vat_id: Bei Ausfuhrlieferung (G) ist die USt-IdNr. des Verkäufers Pflicht (BR-G-2)")
    if "O" in categories and len(categories) > 1:
        res.errors.append("vat_category: 'O' (nicht steuerbar) darf nicht mit anderen USt-Kategorien "
                          "in einer Rechnung gemischt werden (BR-O-11)")

    # Positionen
    ids = [line.id for line in invoice.lines]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        res.errors.append(f"lines: Positionsnummer '{dup}' kommt mehrfach vor")
    for i, line in enumerate(invoice.lines):
        p = f"lines[{i}]"
        if line.unit_price < 0:
            res.errors.append(f"{p}.unit_price: Preis darf nicht negativ sein (BR-27). Für eine "
                              "Minderung negative Menge oder Gutschrift (type_code 381) verwenden.")
        if not re.fullmatch(r"[A-Z0-9]{2,3}", line.unit_code):
            res.errors.append(f"{p}.unit: Einheit '{line.unit_code}' unbekannt. UN/ECE-Code (H87 Stück, HUR Stunde, "
                              "DAY Tag, MON Monat, KGM kg, LS pauschal) oder deutsche Abkürzung (Stk, Std, Tag) angeben")
        if line.period_start and line.period_end and line.period_end < line.period_start:
            res.errors.append(f"{p}: Leistungszeitraum endet vor dem Beginn (BR-30)")
        if xrechnung and invoice.period_start and line.period_start and line.period_start < invoice.period_start:
            res.errors.append(f"{p}.period_start: Liegt vor dem Rechnungszeitraum (billing_period, PEPPOL-EN16931-R110)")
        if xrechnung and invoice.period_end and line.period_end and line.period_end > invoice.period_end:
            res.errors.append(f"{p}.period_end: Liegt nach dem Rechnungszeitraum (billing_period, PEPPOL-EN16931-R111)")
        for ac in line.allowances + line.charges:
            if not (ac.reason or ac.reason_code):
                res.errors.append(f"{p}: {'Zuschlag' if ac.is_charge else 'Abschlag'} braucht einen Grund "
                                  "(reason) oder Code (reason_code) (BR-42/BR-44)")

    # Umsatzsteuer
    for cat in sorted(categories):
        if cat not in VAT_CATEGORIES:
            res.errors.append(f"vat_category: '{cat}' unbekannt. Möglich: "
                              + ", ".join(f"{k} ({v})" for k, v in VAT_CATEGORIES.items()))
            continue
        reason, code = exemption_for(invoice, cat)
        if cat == "E" and not (reason or code):
            res.errors.append("vat_exemptions.E: Grund der Steuerbefreiung angeben (BR-E-10), z. B. "
                              "'Steuerfrei gemäß § 4 Nr. 14 UStG' oder seller.kleinunternehmer = true")
    for i, line in enumerate(invoice.lines):
        p = f"lines[{i}]"
        if line.vat_category == "S" and line.vat_rate <= 0:
            res.errors.append(f"{p}.vat_rate: Kategorie S braucht einen Steuersatz größer 0 (BR-S-5)")
        if line.vat_category in ("Z", "E", "AE", "K", "G", "O") and line.vat_rate != 0:
            res.errors.append(f"{p}.vat_rate: Kategorie {line.vat_category} verlangt 0 % (BR-{line.vat_category}-5)")
        if (line.vat_category == "S" and seller.address.country_code == "DE"
                and line.vat_rate not in (Decimal(19), Decimal(7))):
            res.warnings.append(f"{p}.vat_rate: {line.vat_rate} % ist kein deutscher Steuersatz (19 % oder 7 %)")
    for ac in invoice.allowances + invoice.charges:
        if not (ac.reason or ac.reason_code):
            res.errors.append(f"{'charges' if ac.is_charge else 'allowances'}: Grund (reason) oder Code "
                              "(reason_code) angeben (BR-33/BR-38)")

    # Zahlung
    pay = invoice.payment
    if pay is None:
        if xrechnung:
            res.errors.append("payment: Zahlungsangaben fehlen (BR-DE-1), z. B. "
                              "{means_code: '58', iban: 'DE…', bic: '…'}")
    else:
        if pay.means_code in TRANSFER_CODES and not pay.iban:
            res.errors.append("payment.iban: IBAN für Überweisung fehlt (BR-61/BR-DE-23)")
        for key in ("iban", "debited_iban"):
            value = getattr(pay, key)
            if value and not iban_valid(value):
                res.errors.append(f"payment.{key}: '{value}' ist keine gültige IBAN (Prüfsumme falsch, BR-DE-19)")
        if pay.bic and not BIC_RE.fullmatch(pay.bic):
            res.warnings.append(f"payment.bic: '{pay.bic}' sieht nicht wie eine BIC aus")
        if pay.means_code in DIRECT_DEBIT_CODES:
            if not pay.mandate_id:
                res.errors.append("payment.mandate_id: Mandatsreferenz für Lastschrift fehlt (BR-DE-25)")
            if not pay.creditor_id:
                res.errors.append("payment.creditor_id: Gläubiger-ID für Lastschrift fehlt (BR-DE-30)")
            if not pay.debited_iban:
                res.errors.append("payment.debited_iban: IBAN des belasteten Kontos fehlt (BR-DE-31)")
        if pay.means_code in CARD_CODES:
            if not pay.card_number:
                res.errors.append("payment.card_number: Bei Kartenzahlung die letzten 4–6 Ziffern der "
                                  "Kartennummer angeben (BR-51)")
            elif len(re.sub(r"\D", "", pay.card_number)) > 6:
                res.errors.append("payment.card_number: Nur die letzten 4–6 Ziffern angeben, "
                                  "nie die vollständige Kartennummer (BR-51)")
        if not re.fullmatch(r"\d{1,3}|ZZZ", pay.means_code):
            res.errors.append(f"payment.means_code: '{pay.means_code}' ist kein Code nach UNTDID 4461 "
                              "(58 SEPA-Überweisung, 59 SEPA-Lastschrift, 30 Überweisung, 10 bar, 54 Kreditkarte)")
    if totals.due_payable > 0 and not (invoice.due_date or invoice.payment_terms or invoice.skonto):
        res.errors.append("due_date: Fälligkeitsdatum (due_date), Zahlungsziel (payment_days) oder "
                          "Zahlungsbedingungen (payment_terms) angeben (BR-CO-25)")
    if totals.prepaid > totals.grand_total and totals.grand_total >= 0:
        res.warnings.append("prepaid_amount: Bereits gezahlter Betrag ist höher als der Rechnungsbetrag")
    if totals.grand_total < 0 and invoice.type_code == "380":
        res.warnings.append("type_code: Negativer Rechnungsbetrag – ist eine Gutschrift (381) gemeint?")
    return res
