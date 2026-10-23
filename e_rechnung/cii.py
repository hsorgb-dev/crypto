"""UN/CEFACT Cross Industry Invoice (CII D16B): XRechnung 3.0 CII and the XML inside ZUGFeRD / Factur-X.

Element order follows the CII XSD sequence, which the official validators check."""
import xml.etree.ElementTree as ET

from . import codes
from .model import amount_text, plain, price_text

RSM = 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100'
RAM = 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'
QDT = 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100'
UDT = 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100'
NAMESPACES = {'rsm': RSM, 'ram': RAM, 'qdt': QDT, 'udt': UDT}


def _el(parent, tag, text=None, **attrs):
    prefix, _, name = tag.partition(':')
    node = ET.SubElement(parent, f'{{{NAMESPACES[prefix]}}}{name}', attrs)
    if text is not None:
        node.text = str(text)
    return node


def _opt(parent, tag, text, **attrs):
    if text:
        return _el(parent, tag, text, **attrs)
    return None


def _date(parent, tag, value, prefix='udt'):
    _el(_el(parent, tag), f'{prefix}:DateTimeString', value.strftime('%Y%m%d'), format='102')


def _address(parent, address):
    node = _el(parent, 'ram:PostalTradeAddress')
    _opt(node, 'ram:PostcodeCode', address.postcode)
    _opt(node, 'ram:LineOne', address.street)
    _opt(node, 'ram:LineTwo', address.additional_street)
    _opt(node, 'ram:CityName', address.city)
    _el(node, 'ram:CountryID', address.country)


def _party(parent, tag, party, seller):
    node = _el(parent, tag)
    _opt(node, 'ram:ID', party.id)
    _el(node, 'ram:Name', party.name)
    if seller:
        _opt(node, 'ram:Description', party.legal_info)
    if party.register_id or party.trading_name:
        legal = _el(node, 'ram:SpecifiedLegalOrganization')
        _opt(legal, 'ram:ID', party.register_id)
        _opt(legal, 'ram:TradingBusinessName', party.trading_name)
    contact = party.contact
    if contact.name or contact.phone or contact.email:
        trade_contact = _el(node, 'ram:DefinedTradeContact')
        _opt(trade_contact, 'ram:PersonName', contact.name)
        if contact.phone:
            _el(_el(trade_contact, 'ram:TelephoneUniversalCommunication'), 'ram:CompleteNumber', contact.phone)
        if contact.email:
            _el(_el(trade_contact, 'ram:EmailURIUniversalCommunication'), 'ram:URIID', contact.email)
    _address(node, party.address)
    if party.email:
        _el(_el(node, 'ram:URIUniversalCommunication'), 'ram:URIID', party.email, schemeID='EM')
    if party.vat_id:
        _el(_el(node, 'ram:SpecifiedTaxRegistration'), 'ram:ID', party.vat_id, schemeID='VA')
    if seller and party.tax_number:
        _el(_el(node, 'ram:SpecifiedTaxRegistration'), 'ram:ID', party.tax_number, schemeID='FC')


def _tax(parent, category, rate):
    _el(parent, 'ram:TypeCode', 'VAT')
    _el(parent, 'ram:CategoryCode', category)
    if rate is not None:
        _el(parent, 'ram:RateApplicablePercent', plain(rate))


def build(invoice, guideline=codes.XRECHNUNG_ID):
    """CII XML as bytes. guideline is codes.XRECHNUNG_ID or codes.EN16931_ID (ZUGFeRD profile EN 16931)."""
    for prefix, uri in NAMESPACES.items():
        ET.register_namespace(prefix, uri)
    currency = invoice.currency
    root = ET.Element(f'{{{RSM}}}CrossIndustryInvoice')

    context = _el(root, 'rsm:ExchangedDocumentContext')
    if guideline == codes.XRECHNUNG_ID:
        _el(_el(context, 'ram:BusinessProcessSpecifiedDocumentContextParameter'), 'ram:ID', codes.PEPPOL_PROCESS)
    _el(_el(context, 'ram:GuidelineSpecifiedDocumentContextParameter'), 'ram:ID', guideline)

    document = _el(root, 'rsm:ExchangedDocument')
    _el(document, 'ram:ID', invoice.number)
    _el(document, 'ram:TypeCode', invoice.type_code)
    _date(document, 'ram:IssueDateTime', invoice.issue_date)
    for note in invoice.notes:
        _el(_el(document, 'ram:IncludedNote'), 'ram:Content', note)

    transaction = _el(root, 'rsm:SupplyChainTradeTransaction')
    for line in invoice.lines:
        item = _el(transaction, 'ram:IncludedSupplyChainTradeLineItem')
        line_document = _el(item, 'ram:AssociatedDocumentLineDocument')
        _el(line_document, 'ram:LineID', line.id)
        if line.note:
            _el(_el(line_document, 'ram:IncludedNote'), 'ram:Content', line.note)
        product = _el(item, 'ram:SpecifiedTradeProduct')
        _opt(product, 'ram:SellerAssignedID', line.item_id)
        _opt(product, 'ram:BuyerAssignedID', line.buyer_item_id)
        _el(product, 'ram:Name', line.name)
        _opt(product, 'ram:Description', line.description)
        agreement = _el(item, 'ram:SpecifiedLineTradeAgreement')
        if line.order_line:
            _el(_el(agreement, 'ram:BuyerOrderReferencedDocument'), 'ram:LineID', line.order_line)
        net_price = _el(agreement, 'ram:NetPriceProductTradePrice')
        _el(net_price, 'ram:ChargeAmount', price_text(line.price))
        if line.base_quantity != 1:
            _el(net_price, 'ram:BasisQuantity', plain(line.base_quantity), unitCode=line.unit)
        _el(_el(item, 'ram:SpecifiedLineTradeDelivery'), 'ram:BilledQuantity', plain(line.quantity),
            unitCode=line.unit)
        settlement = _el(item, 'ram:SpecifiedLineTradeSettlement')
        _tax(_el(settlement, 'ram:ApplicableTradeTax'), line.vat_category, line.vat_rate)
        if line.discount_amount:
            allowance = _el(settlement, 'ram:SpecifiedTradeAllowanceCharge')
            _el(_el(allowance, 'ram:ChargeIndicator'), 'udt:Indicator', 'false')
            if line.discount_percent is not None:
                _el(allowance, 'ram:CalculationPercent', plain(line.discount_percent))
                _el(allowance, 'ram:BasisAmount', amount_text(line.gross))
            _el(allowance, 'ram:ActualAmount', amount_text(line.discount_amount))
            _el(allowance, 'ram:Reason', line.discount_reason)
        if line.charge_amount:
            charge = _el(settlement, 'ram:SpecifiedTradeAllowanceCharge')
            _el(_el(charge, 'ram:ChargeIndicator'), 'udt:Indicator', 'true')
            _el(charge, 'ram:ActualAmount', amount_text(line.charge_amount))
            _el(charge, 'ram:Reason', line.charge_reason)
        _el(_el(settlement, 'ram:SpecifiedTradeSettlementLineMonetarySummation'), 'ram:LineTotalAmount',
            amount_text(line.net))

    agreement = _el(transaction, 'ram:ApplicableHeaderTradeAgreement')
    _opt(agreement, 'ram:BuyerReference', invoice.buyer_reference)
    _party(agreement, 'ram:SellerTradeParty', invoice.seller, seller=True)
    _party(agreement, 'ram:BuyerTradeParty', invoice.buyer, seller=False)
    if invoice.order_reference:
        _el(_el(agreement, 'ram:BuyerOrderReferencedDocument'), 'ram:IssuerAssignedID', invoice.order_reference)
    if invoice.contract_reference:
        _el(_el(agreement, 'ram:ContractReferencedDocument'), 'ram:IssuerAssignedID', invoice.contract_reference)
    if invoice.project_reference:
        project = _el(agreement, 'ram:SpecifiedProcuringProject')
        _el(project, 'ram:ID', invoice.project_reference)
        _el(project, 'ram:Name', 'Projekt')

    delivery = _el(transaction, 'ram:ApplicableHeaderTradeDelivery')
    if invoice.delivery_address:
        ship_to = _el(delivery, 'ram:ShipToTradeParty')
        _opt(ship_to, 'ram:Name', invoice.delivery_name)
        _address(ship_to, invoice.delivery_address)
    if invoice.delivery_date:
        _date(_el(delivery, 'ram:ActualDeliverySupplyChainEvent'), 'ram:OccurrenceDateTime', invoice.delivery_date)

    settlement = _el(transaction, 'ram:ApplicableHeaderTradeSettlement')
    payment = invoice.payment
    if payment.means in codes.DIRECT_DEBIT:
        _opt(settlement, 'ram:CreditorReferenceID', payment.creditor_id)
    _opt(settlement, 'ram:PaymentReference', payment.reference)
    _el(settlement, 'ram:InvoiceCurrencyCode', currency)
    means = _el(settlement, 'ram:SpecifiedTradeSettlementPaymentMeans')
    _el(means, 'ram:TypeCode', payment.means)
    _opt(means, 'ram:Information', payment.info or codes.PAYMENT_MEANS[payment.means])
    if payment.means in codes.DIRECT_DEBIT and payment.debtor_iban:
        _el(_el(means, 'ram:PayerPartyDebtorFinancialAccount'), 'ram:IBANID', payment.debtor_iban)
    if payment.means in codes.CREDIT_TRANSFER and payment.iban:
        account = _el(means, 'ram:PayeePartyCreditorFinancialAccount')
        if payment.means == '58' or payment.iban[:2].isalpha():
            _el(account, 'ram:IBANID', payment.iban)
        else:
            _el(account, 'ram:ProprietaryID', payment.iban)
        _opt(account, 'ram:AccountName', payment.account_name)
        if payment.bic:
            _el(_el(means, 'ram:PayeeSpecifiedCreditorFinancialInstitution'), 'ram:BICID', payment.bic)

    for vat in invoice.vat_groups:
        tax = _el(settlement, 'ram:ApplicableTradeTax')
        _el(tax, 'ram:CalculatedAmount', amount_text(vat.amount))
        _el(tax, 'ram:TypeCode', 'VAT')
        _opt(tax, 'ram:ExemptionReason', vat.exemption_reason)
        _el(tax, 'ram:BasisAmount', amount_text(vat.basis))
        _el(tax, 'ram:CategoryCode', vat.category)
        _opt(tax, 'ram:ExemptionReasonCode', vat.exemption_code)
        _el(tax, 'ram:RateApplicablePercent', plain(vat.rate if vat.rate is not None else 0))

    if invoice.period_start or invoice.period_end:
        period = _el(settlement, 'ram:BillingSpecifiedPeriod')
        if invoice.period_start:
            _date(period, 'ram:StartDateTime', invoice.period_start)
        if invoice.period_end:
            _date(period, 'ram:EndDateTime', invoice.period_end)

    for item in invoice.allowances + invoice.charges:
        node = _el(settlement, 'ram:SpecifiedTradeAllowanceCharge')
        _el(_el(node, 'ram:ChargeIndicator'), 'udt:Indicator', 'true' if item.charge else 'false')
        if item.percent is not None and item.base is not None:
            _el(node, 'ram:CalculationPercent', plain(item.percent))
            _el(node, 'ram:BasisAmount', amount_text(item.base))
        _el(node, 'ram:ActualAmount', amount_text(item.amount))
        _el(node, 'ram:Reason', item.reason)
        _tax(_el(node, 'ram:CategoryTradeTax'), item.vat_category, item.vat_rate)

    terms_text = invoice.payment_terms_text
    mandate = payment.mandate_id if payment.means in codes.DIRECT_DEBIT else ''
    if terms_text or invoice.due_date or mandate:
        terms = _el(settlement, 'ram:SpecifiedTradePaymentTerms')
        _opt(terms, 'ram:Description', terms_text)
        if invoice.due_date:
            _date(terms, 'ram:DueDateDateTime', invoice.due_date)
        _opt(terms, 'ram:DirectDebitMandateID', mandate)

    totals = _el(settlement, 'ram:SpecifiedTradeSettlementHeaderMonetarySummation')
    _el(totals, 'ram:LineTotalAmount', amount_text(invoice.line_total))
    if invoice.charges:
        _el(totals, 'ram:ChargeTotalAmount', amount_text(invoice.charge_total))
    if invoice.allowances:
        _el(totals, 'ram:AllowanceTotalAmount', amount_text(invoice.allowance_total))
    _el(totals, 'ram:TaxBasisTotalAmount', amount_text(invoice.tax_basis_total))
    _el(totals, 'ram:TaxTotalAmount', amount_text(invoice.tax_total), currencyID=currency)
    if invoice.rounding:
        _el(totals, 'ram:RoundingAmount', amount_text(invoice.rounding))
    _el(totals, 'ram:GrandTotalAmount', amount_text(invoice.grand_total))
    if invoice.prepaid:
        _el(totals, 'ram:TotalPrepaidAmount', amount_text(invoice.prepaid))
    _el(totals, 'ram:DuePayableAmount', amount_text(invoice.due_amount))

    if invoice.preceding_number:
        preceding = _el(settlement, 'ram:InvoiceReferencedDocument')
        _el(preceding, 'ram:IssuerAssignedID', invoice.preceding_number)
        if invoice.preceding_date:
            _date(preceding, 'ram:FormattedIssueDateTime', invoice.preceding_date, prefix='qdt')

    ET.indent(root)
    return ET.tostring(root, encoding='UTF-8', xml_declaration=True)
