"""UN/CEFACT Cross Industry Invoice (CII, D16B).

Wird für zwei Varianten genutzt:
- XRechnung 3.0 in CII-Syntax
- ZUGFeRD 2.x / Factur-X, Profil EN 16931 (COMFORT), eingebettet ins PDF
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .calc import Totals
from .model import AllowanceCharge, Invoice, Party
from .xmlutil import amount, number, opt, payment_terms_text, price, serialize, sub, ymd

NS_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
NS_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
NS_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
NS_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
for _prefix, _ns in (("rsm", NS_RSM), ("ram", NS_RAM), ("qdt", NS_QDT), ("udt", NS_UDT)):
    ET.register_namespace(_prefix, _ns)

GUIDELINE_XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
GUIDELINE_EN16931 = "urn:cen.eu:en16931:2017"
PROFILE_PEPPOL = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"


def rsm(tag: str) -> str:
    return f"{{{NS_RSM}}}{tag}"


def ram(tag: str) -> str:
    return f"{{{NS_RAM}}}{tag}"


def _date(parent: ET.Element, tag: str, d, qualified: bool = False) -> None:
    el = sub(parent, ram(tag))
    ns = NS_QDT if qualified else NS_UDT
    sub(el, f"{{{ns}}}DateTimeString", ymd(d), format="102")


def _indicator(parent: ET.Element, value: bool) -> None:
    sub(sub(parent, ram("ChargeIndicator")), f"{{{NS_UDT}}}Indicator", "true" if value else "false")


def _address(parent: ET.Element, party: Party) -> None:
    a = party.address
    el = sub(parent, ram("PostalTradeAddress"))
    opt(el, ram("PostcodeCode"), a.postal_code)
    opt(el, ram("LineOne"), a.street)
    opt(el, ram("LineTwo"), a.additional)
    opt(el, ram("CityName"), a.city)
    sub(el, ram("CountryID"), a.country_code)
    opt(el, ram("CountrySubDivisionName"), a.subdivision)


def _party(parent: ET.Element, tag: str, party: Party, seller: bool) -> None:
    p = sub(parent, ram(tag))
    opt(p, ram("ID"), party.id)
    sub(p, ram("Name"), party.name)
    if seller:
        opt(p, ram("Description"), party.legal_info)
    if party.registration_id or party.trading_name:
        lo = sub(p, ram("SpecifiedLegalOrganization"))
        opt(lo, ram("ID"), party.registration_id)
        opt(lo, ram("TradingBusinessName"), party.trading_name)
    c = party.contact
    if c and (c.name or c.phone or c.email):
        ct = sub(p, ram("DefinedTradeContact"))
        opt(ct, ram("PersonName"), c.name)
        if c.phone:
            sub(sub(ct, ram("TelephoneUniversalCommunication")), ram("CompleteNumber"), c.phone)
        if c.email:
            sub(sub(ct, ram("EmailURIUniversalCommunication")), ram("URIID"), c.email)
    _address(p, party)
    if party.electronic_address:
        sub(sub(p, ram("URIUniversalCommunication")), ram("URIID"), party.electronic_address,
            schemeID=party.electronic_address_scheme)
    if party.vat_id:
        sub(sub(p, ram("SpecifiedTaxRegistration")), ram("ID"), party.vat_id, schemeID="VA")
    if seller and party.tax_number:
        sub(sub(p, ram("SpecifiedTaxRegistration")), ram("ID"), party.tax_number, schemeID="FC")


def _period(parent: ET.Element, start, end) -> None:
    if not (start or end):
        return
    per = sub(parent, ram("BillingSpecifiedPeriod"))
    if start:
        _date(per, "StartDateTime", start)
    if end:
        _date(per, "EndDateTime", end)


def _allowance_charge(parent: ET.Element, ac: AllowanceCharge, document_level: bool) -> None:
    el = sub(parent, ram("SpecifiedTradeAllowanceCharge"))
    _indicator(el, ac.is_charge)
    if ac.percent is not None:
        sub(el, ram("CalculationPercent"), number(ac.percent))
    if ac.base_amount is not None:
        sub(el, ram("BasisAmount"), amount(ac.base_amount))
    sub(el, ram("ActualAmount"), amount(ac.amount))
    opt(el, ram("ReasonCode"), ac.reason_code)
    opt(el, ram("Reason"), ac.reason)
    if document_level:
        tax = sub(el, ram("CategoryTradeTax"))
        sub(tax, ram("TypeCode"), "VAT")
        sub(tax, ram("CategoryCode"), ac.vat_category)
        if ac.vat_category != "O":
            sub(tax, ram("RateApplicablePercent"), number(ac.vat_rate))


def build_cii(invoice: Invoice, totals: Totals, xrechnung: bool = True) -> bytes:
    root = ET.Element(rsm("CrossIndustryInvoice"))
    cur = invoice.currency

    ctx = sub(root, rsm("ExchangedDocumentContext"))
    if xrechnung:
        sub(sub(ctx, ram("BusinessProcessSpecifiedDocumentContextParameter")), ram("ID"), PROFILE_PEPPOL)
    sub(sub(ctx, ram("GuidelineSpecifiedDocumentContextParameter")), ram("ID"),
        GUIDELINE_XRECHNUNG if xrechnung else GUIDELINE_EN16931)

    doc = sub(root, rsm("ExchangedDocument"))
    sub(doc, ram("ID"), invoice.number)
    sub(doc, ram("TypeCode"), invoice.type_code)
    _date(doc, "IssueDateTime", invoice.issue_date)
    for note in invoice.notes:
        n = sub(doc, ram("IncludedNote"))
        sub(n, ram("Content"), note.text)
        opt(n, ram("SubjectCode"), note.subject_code)

    tx = sub(root, rsm("SupplyChainTradeTransaction"))
    for line in invoice.lines:
        item = sub(tx, ram("IncludedSupplyChainTradeLineItem"))
        ld = sub(item, ram("AssociatedDocumentLineDocument"))
        sub(ld, ram("LineID"), line.id)
        if line.note:
            sub(sub(ld, ram("IncludedNote")), ram("Content"), line.note)
        prod = sub(item, ram("SpecifiedTradeProduct"))
        opt(prod, ram("SellerAssignedID"), line.seller_item_id)
        opt(prod, ram("BuyerAssignedID"), line.buyer_item_id)
        sub(prod, ram("Name"), line.name)
        opt(prod, ram("Description"), line.description)
        agr = sub(item, ram("SpecifiedLineTradeAgreement"))
        if line.order_line_ref:
            sub(sub(agr, ram("BuyerOrderReferencedDocument")), ram("LineID"), line.order_line_ref)
        net = sub(agr, ram("NetPriceProductTradePrice"))
        sub(net, ram("ChargeAmount"), price(line.unit_price))
        if line.price_base_quantity != 1:
            sub(net, ram("BasisQuantity"), number(line.price_base_quantity), unitCode=line.unit_code)
        sub(sub(item, ram("SpecifiedLineTradeDelivery")), ram("BilledQuantity"), number(line.quantity),
            unitCode=line.unit_code)
        st = sub(item, ram("SpecifiedLineTradeSettlement"))
        tax = sub(st, ram("ApplicableTradeTax"))
        sub(tax, ram("TypeCode"), "VAT")
        sub(tax, ram("CategoryCode"), line.vat_category)
        if line.vat_category != "O":
            sub(tax, ram("RateApplicablePercent"), number(line.vat_rate))
        _period(st, line.period_start, line.period_end)
        for ac in line.allowances + line.charges:
            _allowance_charge(st, ac, document_level=False)
        sub(sub(st, ram("SpecifiedTradeSettlementLineMonetarySummation")), ram("LineTotalAmount"),
            amount(totals.line_net[line.id]))

    agr = sub(tx, ram("ApplicableHeaderTradeAgreement"))
    opt(agr, ram("BuyerReference"), invoice.buyer_reference)
    _party(agr, "SellerTradeParty", invoice.seller, seller=True)
    _party(agr, "BuyerTradeParty", invoice.buyer, seller=False)
    if invoice.order_reference:
        sub(sub(agr, ram("BuyerOrderReferencedDocument")), ram("IssuerAssignedID"), invoice.order_reference)
    if invoice.contract_reference:
        sub(sub(agr, ram("ContractReferencedDocument")), ram("IssuerAssignedID"), invoice.contract_reference)
    if invoice.project_reference:
        proj = sub(agr, ram("SpecifiedProcuringProject"))
        sub(proj, ram("ID"), invoice.project_reference)
        sub(proj, ram("Name"), "Projekt")

    dlv = sub(tx, ram("ApplicableHeaderTradeDelivery"))
    if invoice.delivery_party:
        st = sub(dlv, ram("ShipToTradeParty"))
        opt(st, ram("Name"), invoice.delivery_party.name)
        _address(st, invoice.delivery_party)
    if invoice.delivery_date:
        ev = sub(dlv, ram("ActualDeliverySupplyChainEvent"))
        _date(ev, "OccurrenceDateTime", invoice.delivery_date)

    stl = sub(tx, ram("ApplicableHeaderTradeSettlement"))
    pay = invoice.payment
    if pay and pay.means_code == "59":
        opt(stl, ram("CreditorReferenceID"), pay.creditor_id)
    if pay:
        opt(stl, ram("PaymentReference"), pay.remittance_info)
    sub(stl, ram("InvoiceCurrencyCode"), cur)
    if pay:
        pm = sub(stl, ram("SpecifiedTradeSettlementPaymentMeans"))
        sub(pm, ram("TypeCode"), pay.means_code)
        opt(pm, ram("Information"), pay.info)
        if pay.card_number:
            card = sub(pm, ram("ApplicableTradeSettlementFinancialCard"))
            sub(card, ram("ID"), pay.card_number)
            opt(card, ram("CardholderName"), pay.card_holder)
        if pay.means_code == "59" and pay.debited_iban:
            sub(sub(pm, ram("PayerPartyDebtorFinancialAccount")), ram("IBANID"), pay.debited_iban)
        if pay.iban and pay.means_code != "59":
            acc = sub(pm, ram("PayeePartyCreditorFinancialAccount"))
            sub(acc, ram("IBANID"), pay.iban)
            opt(acc, ram("AccountName"), pay.account_name)
            if pay.bic:
                sub(sub(pm, ram("PayeeSpecifiedCreditorFinancialInstitution")), ram("BICID"), pay.bic)

    for g in totals.vat_groups:
        tax = sub(stl, ram("ApplicableTradeTax"))
        sub(tax, ram("CalculatedAmount"), amount(g.tax))
        sub(tax, ram("TypeCode"), "VAT")
        opt(tax, ram("ExemptionReason"), g.exemption_reason)
        sub(tax, ram("BasisAmount"), amount(g.taxable))
        sub(tax, ram("CategoryCode"), g.category)
        opt(tax, ram("ExemptionReasonCode"), g.exemption_code)
        if g.category != "O" or xrechnung:  # BR-DE-14
            sub(tax, ram("RateApplicablePercent"), number(g.rate))

    _period(stl, invoice.period_start, invoice.period_end)
    for ac in invoice.allowances + invoice.charges:
        _allowance_charge(stl, ac, document_level=True)

    terms = payment_terms_text(invoice.payment_terms, invoice.skonto, structured=xrechnung)
    mandate = pay.mandate_id if pay and pay.means_code == "59" else None
    if terms or invoice.due_date or mandate:
        pt = sub(stl, ram("SpecifiedTradePaymentTerms"))
        opt(pt, ram("Description"), terms)
        if invoice.due_date:
            _date(pt, "DueDateDateTime", invoice.due_date)
        opt(pt, ram("DirectDebitMandateID"), mandate)

    ms = sub(stl, ram("SpecifiedTradeSettlementHeaderMonetarySummation"))
    sub(ms, ram("LineTotalAmount"), amount(totals.line_total))
    if invoice.charges:
        sub(ms, ram("ChargeTotalAmount"), amount(totals.charge_total))
    if invoice.allowances:
        sub(ms, ram("AllowanceTotalAmount"), amount(totals.allowance_total))
    sub(ms, ram("TaxBasisTotalAmount"), amount(totals.tax_basis_total))
    sub(ms, ram("TaxTotalAmount"), amount(totals.tax_total), currencyID=cur)
    if totals.rounding:
        sub(ms, ram("RoundingAmount"), amount(totals.rounding))
    sub(ms, ram("GrandTotalAmount"), amount(totals.grand_total))
    if totals.prepaid:
        sub(ms, ram("TotalPrepaidAmount"), amount(totals.prepaid))
    sub(ms, ram("DuePayableAmount"), amount(totals.due_payable))

    if invoice.preceding_invoice_number:
        ref = sub(stl, ram("InvoiceReferencedDocument"))
        sub(ref, ram("IssuerAssignedID"), invoice.preceding_invoice_number)
        if invoice.preceding_invoice_date:
            _date(ref, "FormattedIssueDateTime", invoice.preceding_invoice_date, qualified=True)

    return serialize(root)
