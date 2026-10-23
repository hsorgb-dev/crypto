"""OASIS UBL 2.1: XRechnung 3.0 UBL. Type 381 becomes a UBL CreditNote, everything else an Invoice.

Element order follows the UBL 2.1 XSD sequence, which the official validators check."""
import xml.etree.ElementTree as ET

from . import codes
from .model import amount_text, plain, price_text

INVOICE = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
CREDIT_NOTE = 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2'
CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NAMESPACES = {'cac': CAC, 'cbc': CBC}


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


def _address(parent, tag, address):
    node = _el(parent, tag)
    _opt(node, 'cbc:StreetName', address.street)
    _opt(node, 'cbc:AdditionalStreetName', address.additional_street)
    _opt(node, 'cbc:CityName', address.city)
    _opt(node, 'cbc:PostalZone', address.postcode)
    _el(_el(node, 'cac:Country'), 'cbc:IdentificationCode', address.country)


def _tax_scheme(parent):
    _el(_el(parent, 'cac:TaxScheme'), 'cbc:ID', 'VAT')


def _tax_category(parent, tag, category, rate):
    node = _el(parent, tag)
    _el(node, 'cbc:ID', category)
    if rate is not None:
        _el(node, 'cbc:Percent', plain(rate))
    _tax_scheme(node)


def _party(parent, tag, party, seller, creditor_id=''):
    node = _el(_el(parent, tag), 'cac:Party')
    _opt(node, 'cbc:EndpointID', party.email, schemeID='EM')
    if party.id:
        _el(_el(node, 'cac:PartyIdentification'), 'cbc:ID', party.id)
    if creditor_id:
        _el(_el(node, 'cac:PartyIdentification'), 'cbc:ID', creditor_id, schemeID='SEPA')
    if party.trading_name:
        _el(_el(node, 'cac:PartyName'), 'cbc:Name', party.trading_name)
    _address(node, 'cac:PostalAddress', party.address)
    if party.vat_id:
        scheme = _el(node, 'cac:PartyTaxScheme')
        _el(scheme, 'cbc:CompanyID', party.vat_id)
        _tax_scheme(scheme)
    if seller and party.tax_number:
        scheme = _el(node, 'cac:PartyTaxScheme')
        _el(scheme, 'cbc:CompanyID', party.tax_number)
        _el(_el(scheme, 'cac:TaxScheme'), 'cbc:ID', 'FC')
    legal = _el(node, 'cac:PartyLegalEntity')
    _el(legal, 'cbc:RegistrationName', party.name)
    _opt(legal, 'cbc:CompanyID', party.register_id)
    if seller:
        _opt(legal, 'cbc:CompanyLegalForm', party.legal_info)
    contact = party.contact
    if contact.name or contact.phone or contact.email:
        node_contact = _el(node, 'cac:Contact')
        _opt(node_contact, 'cbc:Name', contact.name)
        _opt(node_contact, 'cbc:Telephone', contact.phone)
        _opt(node_contact, 'cbc:ElectronicMail', contact.email)


def build(invoice, customization=codes.XRECHNUNG_ID):
    """UBL XML as bytes."""
    credit = invoice.is_credit_note
    root_ns = CREDIT_NOTE if credit else INVOICE
    ET.register_namespace('ubl', root_ns)
    for prefix, uri in NAMESPACES.items():
        ET.register_namespace(prefix, uri)
    currency = invoice.currency
    payment = invoice.payment
    root = ET.Element(f'{{{root_ns}}}{"CreditNote" if credit else "Invoice"}')

    _el(root, 'cbc:CustomizationID', customization)
    _el(root, 'cbc:ProfileID', codes.PEPPOL_PROCESS)
    _el(root, 'cbc:ID', invoice.number)
    _el(root, 'cbc:IssueDate', invoice.issue_date.isoformat())
    if not credit and invoice.due_date:
        _el(root, 'cbc:DueDate', invoice.due_date.isoformat())
    _el(root, 'cbc:CreditNoteTypeCode' if credit else 'cbc:InvoiceTypeCode', invoice.type_code)
    for note in invoice.notes:
        _el(root, 'cbc:Note', note)
    _el(root, 'cbc:DocumentCurrencyCode', currency)
    _opt(root, 'cbc:BuyerReference', invoice.buyer_reference)
    if invoice.period_start or invoice.period_end:
        period = _el(root, 'cac:InvoicePeriod')
        _opt(period, 'cbc:StartDate', invoice.period_start and invoice.period_start.isoformat())
        _opt(period, 'cbc:EndDate', invoice.period_end and invoice.period_end.isoformat())
    if invoice.order_reference:
        _el(_el(root, 'cac:OrderReference'), 'cbc:ID', invoice.order_reference)
    if invoice.preceding_number:
        reference = _el(_el(root, 'cac:BillingReference'), 'cac:InvoiceDocumentReference')
        _el(reference, 'cbc:ID', invoice.preceding_number)
        _opt(reference, 'cbc:IssueDate', invoice.preceding_date and invoice.preceding_date.isoformat())
    if invoice.contract_reference:
        _el(_el(root, 'cac:ContractDocumentReference'), 'cbc:ID', invoice.contract_reference)
    if invoice.project_reference:
        if credit:
            reference = _el(root, 'cac:AdditionalDocumentReference')
            _el(reference, 'cbc:ID', invoice.project_reference)
            _el(reference, 'cbc:DocumentTypeCode', '50')
        else:
            _el(_el(root, 'cac:ProjectReference'), 'cbc:ID', invoice.project_reference)

    creditor_id = payment.creditor_id if payment.means in codes.DIRECT_DEBIT else ''
    _party(root, 'cac:AccountingSupplierParty', invoice.seller, seller=True, creditor_id=creditor_id)
    _party(root, 'cac:AccountingCustomerParty', invoice.buyer, seller=False)

    if invoice.delivery_date or invoice.delivery_address:
        delivery = _el(root, 'cac:Delivery')
        _opt(delivery, 'cbc:ActualDeliveryDate', invoice.delivery_date and invoice.delivery_date.isoformat())
        if invoice.delivery_address:
            _address(_el(delivery, 'cac:DeliveryLocation'), 'cac:Address', invoice.delivery_address)
            if invoice.delivery_name:
                _el(_el(_el(delivery, 'cac:DeliveryParty'), 'cac:PartyName'), 'cbc:Name', invoice.delivery_name)

    means = _el(root, 'cac:PaymentMeans')
    _el(means, 'cbc:PaymentMeansCode', payment.means, name=payment.info or codes.PAYMENT_MEANS[payment.means])
    if credit and invoice.due_date:
        _el(means, 'cbc:PaymentDueDate', invoice.due_date.isoformat())
    _opt(means, 'cbc:PaymentID', payment.reference)
    if payment.means in codes.CREDIT_TRANSFER and payment.iban:
        account = _el(means, 'cac:PayeeFinancialAccount')
        _el(account, 'cbc:ID', payment.iban)
        _opt(account, 'cbc:Name', payment.account_name)
        if payment.bic:
            _el(_el(account, 'cac:FinancialInstitutionBranch'), 'cbc:ID', payment.bic)
    if payment.means in codes.DIRECT_DEBIT:
        mandate = _el(means, 'cac:PaymentMandate')
        _opt(mandate, 'cbc:ID', payment.mandate_id)
        if payment.debtor_iban:
            _el(_el(mandate, 'cac:PayerFinancialAccount'), 'cbc:ID', payment.debtor_iban)
    terms_text = invoice.payment_terms_text
    if terms_text:
        _el(_el(root, 'cac:PaymentTerms'), 'cbc:Note', terms_text)

    for item in invoice.allowances + invoice.charges:
        node = _el(root, 'cac:AllowanceCharge')
        _el(node, 'cbc:ChargeIndicator', 'true' if item.charge else 'false')
        _el(node, 'cbc:AllowanceChargeReason', item.reason)
        if item.percent is not None and item.base is not None:
            _el(node, 'cbc:MultiplierFactorNumeric', plain(item.percent))
        _el(node, 'cbc:Amount', amount_text(item.amount), currencyID=currency)
        if item.percent is not None and item.base is not None:
            _el(node, 'cbc:BaseAmount', amount_text(item.base), currencyID=currency)
        _tax_category(node, 'cac:TaxCategory', item.vat_category, item.vat_rate)

    tax_total = _el(root, 'cac:TaxTotal')
    _el(tax_total, 'cbc:TaxAmount', amount_text(invoice.tax_total), currencyID=currency)
    for vat in invoice.vat_groups:
        subtotal = _el(tax_total, 'cac:TaxSubtotal')
        _el(subtotal, 'cbc:TaxableAmount', amount_text(vat.basis), currencyID=currency)
        _el(subtotal, 'cbc:TaxAmount', amount_text(vat.amount), currencyID=currency)
        category = _el(subtotal, 'cac:TaxCategory')
        _el(category, 'cbc:ID', vat.category)
        _el(category, 'cbc:Percent', plain(vat.rate if vat.rate is not None else 0))
        _opt(category, 'cbc:TaxExemptionReasonCode', vat.exemption_code)
        _opt(category, 'cbc:TaxExemptionReason', vat.exemption_reason)
        _tax_scheme(category)

    totals = _el(root, 'cac:LegalMonetaryTotal')
    _el(totals, 'cbc:LineExtensionAmount', amount_text(invoice.line_total), currencyID=currency)
    _el(totals, 'cbc:TaxExclusiveAmount', amount_text(invoice.tax_basis_total), currencyID=currency)
    _el(totals, 'cbc:TaxInclusiveAmount', amount_text(invoice.grand_total), currencyID=currency)
    if invoice.allowances:
        _el(totals, 'cbc:AllowanceTotalAmount', amount_text(invoice.allowance_total), currencyID=currency)
    if invoice.charges:
        _el(totals, 'cbc:ChargeTotalAmount', amount_text(invoice.charge_total), currencyID=currency)
    if invoice.prepaid:
        _el(totals, 'cbc:PrepaidAmount', amount_text(invoice.prepaid), currencyID=currency)
    if invoice.rounding:
        _el(totals, 'cbc:PayableRoundingAmount', amount_text(invoice.rounding), currencyID=currency)
    _el(totals, 'cbc:PayableAmount', amount_text(invoice.due_amount), currencyID=currency)

    for line in invoice.lines:
        node = _el(root, 'cac:CreditNoteLine' if credit else 'cac:InvoiceLine')
        _el(node, 'cbc:ID', line.id)
        _opt(node, 'cbc:Note', line.note)
        _el(node, 'cbc:CreditedQuantity' if credit else 'cbc:InvoicedQuantity', plain(line.quantity),
            unitCode=line.unit)
        _el(node, 'cbc:LineExtensionAmount', amount_text(line.net), currencyID=currency)
        if line.order_line:
            _el(_el(node, 'cac:OrderLineReference'), 'cbc:LineID', line.order_line)
        if line.discount_amount:
            allowance = _el(node, 'cac:AllowanceCharge')
            _el(allowance, 'cbc:ChargeIndicator', 'false')
            _el(allowance, 'cbc:AllowanceChargeReason', line.discount_reason)
            if line.discount_percent is not None:
                _el(allowance, 'cbc:MultiplierFactorNumeric', plain(line.discount_percent))
            _el(allowance, 'cbc:Amount', amount_text(line.discount_amount), currencyID=currency)
            if line.discount_percent is not None:
                _el(allowance, 'cbc:BaseAmount', amount_text(line.gross), currencyID=currency)
        if line.charge_amount:
            charge = _el(node, 'cac:AllowanceCharge')
            _el(charge, 'cbc:ChargeIndicator', 'true')
            _el(charge, 'cbc:AllowanceChargeReason', line.charge_reason)
            _el(charge, 'cbc:Amount', amount_text(line.charge_amount), currencyID=currency)
        item = _el(node, 'cac:Item')
        _opt(item, 'cbc:Description', line.description)
        _el(item, 'cbc:Name', line.name)
        if line.buyer_item_id:
            _el(_el(item, 'cac:BuyersItemIdentification'), 'cbc:ID', line.buyer_item_id)
        if line.item_id:
            _el(_el(item, 'cac:SellersItemIdentification'), 'cbc:ID', line.item_id)
        _tax_category(item, 'cac:ClassifiedTaxCategory', line.vat_category, line.vat_rate)
        price = _el(node, 'cac:Price')
        _el(price, 'cbc:PriceAmount', price_text(line.price), currencyID=currency)
        if line.base_quantity != 1:
            _el(price, 'cbc:BaseQuantity', plain(line.base_quantity), unitCode=line.unit)

    ET.indent(root)
    return ET.tostring(root, encoding='UTF-8', xml_declaration=True)
