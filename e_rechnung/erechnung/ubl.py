"""XRechnung 3.0 in der UBL-2.1-Syntax (Invoice bzw. CreditNote).

Die Reihenfolge der Elemente folgt dem UBL-2.1-Schema; sie ist dort
verbindlich und wird beim Test gegen das XSD geprüft.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .calc import Totals
from .model import AllowanceCharge, Invoice, Party
from .xmlutil import amount, iso, number, opt, payment_terms_text, price, serialize, sub

NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CREDIT = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
ET.register_namespace("cac", NS_CAC)
ET.register_namespace("cbc", NS_CBC)

CUSTOMIZATION_XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PROFILE_PEPPOL = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"


def cac(tag: str) -> str:
    return f"{{{NS_CAC}}}{tag}"


def cbc(tag: str) -> str:
    return f"{{{NS_CBC}}}{tag}"


def _tax_category(parent: ET.Element, tag: str, category: str, rate, reason=None, code=None,
                  breakdown: bool = False) -> None:
    tc = sub(parent, cac(tag))
    sub(tc, cbc("ID"), category)
    if category != "O" or breakdown:  # XRechnung verlangt den Satz in der Aufschlüsselung (BR-DE-14)
        sub(tc, cbc("Percent"), number(rate))
    opt(tc, cbc("TaxExemptionReasonCode"), code)
    opt(tc, cbc("TaxExemptionReason"), reason)
    sub(sub(tc, cac("TaxScheme")), cbc("ID"), "VAT")


def _address(parent: ET.Element, tag: str, party: Party) -> None:
    a = party.address
    el = sub(parent, cac(tag))
    opt(el, cbc("StreetName"), a.street)
    opt(el, cbc("AdditionalStreetName"), a.additional)
    opt(el, cbc("CityName"), a.city)
    opt(el, cbc("PostalZone"), a.postal_code)
    opt(el, cbc("CountrySubentity"), a.subdivision)
    sub(sub(el, cac("Country")), cbc("IdentificationCode"), a.country_code)


def _party(parent: ET.Element, wrapper: str, party: Party, creditor_id: str | None = None) -> None:
    p = sub(sub(parent, cac(wrapper)), cac("Party"))
    if party.electronic_address:
        sub(p, cbc("EndpointID"), party.electronic_address, schemeID=party.electronic_address_scheme)
    if party.id:
        sub(sub(p, cac("PartyIdentification")), cbc("ID"), party.id)
    if creditor_id:
        sub(sub(p, cac("PartyIdentification")), cbc("ID"), creditor_id, schemeID="SEPA")
    if party.trading_name:
        sub(sub(p, cac("PartyName")), cbc("Name"), party.trading_name)
    _address(p, "PostalAddress", party)
    if party.vat_id:
        ts = sub(p, cac("PartyTaxScheme"))
        sub(ts, cbc("CompanyID"), party.vat_id)
        sub(sub(ts, cac("TaxScheme")), cbc("ID"), "VAT")
    if party.tax_number and wrapper == "AccountingSupplierParty":
        ts = sub(p, cac("PartyTaxScheme"))
        sub(ts, cbc("CompanyID"), party.tax_number)
        sub(sub(ts, cac("TaxScheme")), cbc("ID"), "FC")
    le = sub(p, cac("PartyLegalEntity"))
    sub(le, cbc("RegistrationName"), party.name)
    opt(le, cbc("CompanyID"), party.registration_id)
    if wrapper == "AccountingSupplierParty":
        opt(le, cbc("CompanyLegalForm"), party.legal_info)
    c = party.contact
    if c and (c.name or c.phone or c.email):
        ct = sub(p, cac("Contact"))
        opt(ct, cbc("Name"), c.name)
        opt(ct, cbc("Telephone"), c.phone)
        opt(ct, cbc("ElectronicMail"), c.email)


def _allowance_charge(parent: ET.Element, ac: AllowanceCharge, currency: str, document_level: bool) -> None:
    el = sub(parent, cac("AllowanceCharge"))
    sub(el, cbc("ChargeIndicator"), "true" if ac.is_charge else "false")
    opt(el, cbc("AllowanceChargeReasonCode"), ac.reason_code)
    opt(el, cbc("AllowanceChargeReason"), ac.reason)
    if ac.percent is not None:
        sub(el, cbc("MultiplierFactorNumeric"), number(ac.percent))
    sub(el, cbc("Amount"), amount(ac.amount), currencyID=currency)
    if ac.base_amount is not None:
        sub(el, cbc("BaseAmount"), amount(ac.base_amount), currencyID=currency)
    if document_level:
        _tax_category(el, "TaxCategory", ac.vat_category, ac.vat_rate)


def build_ubl(invoice: Invoice, totals: Totals) -> bytes:
    credit = invoice.is_credit_note
    ns = NS_CREDIT if credit else NS_INVOICE
    # Wurzel im Default-Namespace; ElementTree kann default_namespace nicht mit
    # unqualifizierten Attributen (currencyID, schemeID) kombinieren.
    root = ET.Element("CreditNote" if credit else "Invoice", xmlns=ns)
    cur = invoice.currency

    sub(root, cbc("CustomizationID"), CUSTOMIZATION_XRECHNUNG)
    sub(root, cbc("ProfileID"), PROFILE_PEPPOL)
    sub(root, cbc("ID"), invoice.number)
    sub(root, cbc("IssueDate"), iso(invoice.issue_date))
    if credit:
        sub(root, cbc("CreditNoteTypeCode"), invoice.type_code)
    else:
        if invoice.due_date:
            sub(root, cbc("DueDate"), iso(invoice.due_date))
        sub(root, cbc("InvoiceTypeCode"), invoice.type_code)
    for note in invoice.notes:
        text = f"#{note.subject_code}#{note.text}" if note.subject_code else note.text
        sub(root, cbc("Note"), text)
    sub(root, cbc("DocumentCurrencyCode"), cur)
    opt(root, cbc("BuyerReference"), invoice.buyer_reference)
    if invoice.period_start or invoice.period_end:
        per = sub(root, cac("InvoicePeriod"))
        if invoice.period_start:
            sub(per, cbc("StartDate"), iso(invoice.period_start))
        if invoice.period_end:
            sub(per, cbc("EndDate"), iso(invoice.period_end))
    if invoice.order_reference:
        sub(sub(root, cac("OrderReference")), cbc("ID"), invoice.order_reference)
    if invoice.preceding_invoice_number:
        ref = sub(sub(root, cac("BillingReference")), cac("InvoiceDocumentReference"))
        sub(ref, cbc("ID"), invoice.preceding_invoice_number)
        if invoice.preceding_invoice_date:
            sub(ref, cbc("IssueDate"), iso(invoice.preceding_invoice_date))
    if invoice.contract_reference:
        sub(sub(root, cac("ContractDocumentReference")), cbc("ID"), invoice.contract_reference)
    if invoice.project_reference:
        if credit:  # CreditNote kennt kein ProjectReference (EN 16931: DocumentTypeCode 50)
            ref = sub(root, cac("AdditionalDocumentReference"))
            sub(ref, cbc("ID"), invoice.project_reference)
            sub(ref, cbc("DocumentTypeCode"), "50")
        else:
            sub(sub(root, cac("ProjectReference")), cbc("ID"), invoice.project_reference)

    pay = invoice.payment
    creditor_id = pay.creditor_id if pay and pay.means_code == "59" else None
    _party(root, "AccountingSupplierParty", invoice.seller, creditor_id)
    _party(root, "AccountingCustomerParty", invoice.buyer)

    if invoice.delivery_date or invoice.delivery_party:
        d = sub(root, cac("Delivery"))
        if invoice.delivery_date:
            sub(d, cbc("ActualDeliveryDate"), iso(invoice.delivery_date))
        if invoice.delivery_party:
            loc = sub(d, cac("DeliveryLocation"))
            _address(loc, "Address", invoice.delivery_party)
            if invoice.delivery_party.name:
                sub(sub(sub(d, cac("DeliveryParty")), cac("PartyName")), cbc("Name"), invoice.delivery_party.name)

    if pay:
        pm = sub(root, cac("PaymentMeans"))
        sub(pm, cbc("PaymentMeansCode"), pay.means_code, name=pay.info)
        if credit and invoice.due_date:
            sub(pm, cbc("PaymentDueDate"), iso(invoice.due_date))
        opt(pm, cbc("PaymentID"), pay.remittance_info)
        if pay.card_number:
            card = sub(pm, cac("CardAccount"))
            sub(card, cbc("PrimaryAccountNumberID"), pay.card_number)
            sub(card, cbc("NetworkID"), "NA")  # im UBL-Schema Pflicht, in EN 16931 nicht belegt
            opt(card, cbc("HolderName"), pay.card_holder)
        if pay.iban and pay.means_code != "59":
            acc = sub(pm, cac("PayeeFinancialAccount"))
            sub(acc, cbc("ID"), pay.iban)
            opt(acc, cbc("Name"), pay.account_name)
            if pay.bic:
                sub(sub(acc, cac("FinancialInstitutionBranch")), cbc("ID"), pay.bic)
        if pay.means_code == "59":
            mandate = sub(pm, cac("PaymentMandate"))
            opt(mandate, cbc("ID"), pay.mandate_id)
            if pay.debited_iban:
                sub(sub(mandate, cac("PayerFinancialAccount")), cbc("ID"), pay.debited_iban)

    terms = payment_terms_text(invoice.payment_terms, invoice.skonto, structured=True)
    if terms:
        sub(sub(root, cac("PaymentTerms")), cbc("Note"), terms)

    for ac in invoice.allowances + invoice.charges:
        _allowance_charge(root, ac, cur, document_level=True)

    tt = sub(root, cac("TaxTotal"))
    sub(tt, cbc("TaxAmount"), amount(totals.tax_total), currencyID=cur)
    for g in totals.vat_groups:
        st = sub(tt, cac("TaxSubtotal"))
        sub(st, cbc("TaxableAmount"), amount(g.taxable), currencyID=cur)
        sub(st, cbc("TaxAmount"), amount(g.tax), currencyID=cur)
        _tax_category(st, "TaxCategory", g.category, g.rate, g.exemption_reason, g.exemption_code,
                      breakdown=True)

    mt = sub(root, cac("LegalMonetaryTotal"))
    sub(mt, cbc("LineExtensionAmount"), amount(totals.line_total), currencyID=cur)
    sub(mt, cbc("TaxExclusiveAmount"), amount(totals.tax_basis_total), currencyID=cur)
    sub(mt, cbc("TaxInclusiveAmount"), amount(totals.grand_total), currencyID=cur)
    if invoice.allowances:
        sub(mt, cbc("AllowanceTotalAmount"), amount(totals.allowance_total), currencyID=cur)
    if invoice.charges:
        sub(mt, cbc("ChargeTotalAmount"), amount(totals.charge_total), currencyID=cur)
    if totals.prepaid:
        sub(mt, cbc("PrepaidAmount"), amount(totals.prepaid), currencyID=cur)
    if totals.rounding:
        sub(mt, cbc("PayableRoundingAmount"), amount(totals.rounding), currencyID=cur)
    sub(mt, cbc("PayableAmount"), amount(totals.due_payable), currencyID=cur)

    for line in invoice.lines:
        el = sub(root, cac("CreditNoteLine" if credit else "InvoiceLine"))
        sub(el, cbc("ID"), line.id)
        opt(el, cbc("Note"), line.note)
        sub(el, cbc("CreditedQuantity" if credit else "InvoicedQuantity"), number(line.quantity),
            unitCode=line.unit_code)
        sub(el, cbc("LineExtensionAmount"), amount(totals.line_net[line.id]), currencyID=cur)
        if line.period_start or line.period_end:
            per = sub(el, cac("InvoicePeriod"))
            if line.period_start:
                sub(per, cbc("StartDate"), iso(line.period_start))
            if line.period_end:
                sub(per, cbc("EndDate"), iso(line.period_end))
        if line.order_line_ref:
            sub(sub(el, cac("OrderLineReference")), cbc("LineID"), line.order_line_ref)
        for ac in line.allowances + line.charges:
            _allowance_charge(el, ac, cur, document_level=False)
        item = sub(el, cac("Item"))
        opt(item, cbc("Description"), line.description)
        sub(item, cbc("Name"), line.name)
        if line.buyer_item_id:
            sub(sub(item, cac("BuyersItemIdentification")), cbc("ID"), line.buyer_item_id)
        if line.seller_item_id:
            sub(sub(item, cac("SellersItemIdentification")), cbc("ID"), line.seller_item_id)
        _tax_category(item, "ClassifiedTaxCategory", line.vat_category, line.vat_rate)
        pr = sub(el, cac("Price"))
        sub(pr, cbc("PriceAmount"), price(line.unit_price), currencyID=cur)
        if line.price_base_quantity != 1:
            sub(pr, cbc("BaseQuantity"), number(line.price_base_quantity), unitCode=line.unit_code)

    return serialize(root)
