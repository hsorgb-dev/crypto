"""Reads an e-invoice (XRechnung CII/UBL XML, or a ZUGFeRD / Factur-X PDF) back into the input format.

The result is the same dict that create() takes, so a received invoice can be shown, checked
and converted into another format. The totals exactly as the document states them are kept
under "stated", so a check can compare them with a fresh calculation."""
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

from . import cii, ubl
from .model import InvoiceError, Issue

CII_NS = cii.NAMESPACES
UBL_NS = {'cac': ubl.CAC, 'cbc': ubl.CBC}


def read(content):
    """dict in the input format plus 'syntax', 'specification' and 'stated'. Accepts XML or PDF bytes."""
    if isinstance(content, str):
        content = content.encode('utf-8')
    if content[:5] == b'%PDF-':
        from .pdf import embedded_xml
        xml = embedded_xml(content)
        if xml is None:
            raise InvoiceError([Issue('fehler', 'datei', 'Das PDF enthält keine eingebettete Rechnungs-XML '
                                      '(kein ZUGFeRD / Factur-X).')])
        data = read(xml)
        data['container'] = 'PDF (ZUGFeRD / Factur-X)'
        return data
    head = content[:4096].lower()
    if b'<!doctype' in head or b'<!entity' in content.lower():
        raise InvoiceError([Issue('fehler', 'datei', 'XML mit DOCTYPE/ENTITY wird aus Sicherheitsgründen '
                                  'nicht gelesen.')])
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise InvoiceError([Issue('fehler', 'datei', f'Keine gültige XML-Datei: {exc}.')]) from None
    name = root.tag.rpartition('}')[2]
    if name == 'CrossIndustryInvoice':
        return _read_cii(root)
    if name in ('Invoice', 'CreditNote'):
        return _read_ubl(root, credit=name == 'CreditNote')
    raise InvoiceError([Issue('fehler', 'datei', f'Unbekanntes Format (Wurzelelement {name!r}). Erwartet '
                              'wird XRechnung / ZUGFeRD 2.x (CII) oder UBL.')])


class _Doc:
    def __init__(self, namespaces):
        self.ns = namespaces

    def all(self, node, path):
        return node.findall(path, self.ns) if node is not None else []

    def node(self, node, path):
        return node.find(path, self.ns) if node is not None else None

    def text(self, node, path='.'):
        found = node.find(path, self.ns) if node is not None else None
        return (found.text or '').strip() if found is not None and found.text else ''

    def attr(self, node, path, name):
        found = node.find(path, self.ns) if node is not None else None
        return found.get(name, '') if found is not None else ''


def _date(text):
    text = (text or '').strip()
    if re.fullmatch(r'\d{8}', text):
        return f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return text[:10]


def _clean(value):
    """Drop empty strings, dicts and lists so the result stays readable."""
    if isinstance(value, dict):
        cleaned = {key: _clean(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in ('', None, {}, [])}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _line_discount(allowances):
    """One discount per line in the input format: several line allowances are added up."""
    if not allowances:
        return {}
    if len(allowances) == 1:
        amount, percent, reason = allowances[0]
        return {'amount': amount, 'percent': percent, 'reason': reason}
    total = sum(Decimal(amount or '0') for amount, _, _ in allowances)
    return {'amount': str(total), 'reason': ', '.join(reason for _, _, reason in allowances if reason)}


def _line_surcharge(charges):
    if not charges:
        return {}
    total = sum(Decimal(amount or '0') for amount, _ in charges)
    return {'amount': str(total) if len(charges) > 1 else charges[0][0],
            'reason': ', '.join(reason for _, reason in charges if reason)}


def _terms(text, payment):
    """Split BT-20 into free text and the XRechnung Skonto line (#SKONTO#TAGE=..#PROZENT=..#)."""
    match = re.search(r'#SKONTO#TAGE=(\d+)#PROZENT=([\d.]+)#', text or '')
    if match:
        payment['discount'] = {'days': int(match.group(1)), 'percent': match.group(2)}
        text = re.sub(r'Bei Zahlung innerhalb von \d+ Tagen [\d,.]+ % Skonto\.', '', text)
    lines = [line.strip() for line in (text or '').split('\n') if line.strip() and not line.strip().startswith('#')]
    payment['terms'] = ' '.join(lines)


def _read_cii(root):
    d = _Doc(CII_NS)
    document = d.node(root, 'rsm:ExchangedDocument')
    transaction = d.node(root, 'rsm:SupplyChainTradeTransaction')
    agreement = d.node(transaction, 'ram:ApplicableHeaderTradeAgreement')
    delivery = d.node(transaction, 'ram:ApplicableHeaderTradeDelivery')
    settlement = d.node(transaction, 'ram:ApplicableHeaderTradeSettlement')

    def party(node, seller):
        if node is None:
            return {}
        tax_ids = {item.get('schemeID', ''): (item.text or '').strip()
                   for item in d.all(node, 'ram:SpecifiedTaxRegistration/ram:ID')}
        address = d.node(node, 'ram:PostalTradeAddress')
        contact = d.node(node, 'ram:DefinedTradeContact')
        result = {
            'name': d.text(node, 'ram:Name'),
            'trading_name': d.text(node, 'ram:SpecifiedLegalOrganization/ram:TradingBusinessName'),
            'id': d.text(node, 'ram:ID') or d.text(node, 'ram:GlobalID'),
            'register_id': d.text(node, 'ram:SpecifiedLegalOrganization/ram:ID'),
            'vat_id': tax_ids.get('VA', ''),
            'street': d.text(address, 'ram:LineOne'),
            'additional_street': ' '.join(filter(None, (d.text(address, 'ram:LineTwo'),
                                                        d.text(address, 'ram:LineThree')))),
            'postcode': d.text(address, 'ram:PostcodeCode'),
            'city': d.text(address, 'ram:CityName'),
            'country': d.text(address, 'ram:CountryID'),
            'email': d.text(node, 'ram:URIUniversalCommunication/ram:URIID'),
            'contact': {'name': d.text(contact, 'ram:PersonName') or d.text(contact, 'ram:DepartmentName'),
                        'phone': d.text(contact, 'ram:TelephoneUniversalCommunication/ram:CompleteNumber'),
                        'email': d.text(contact, 'ram:EmailURIUniversalCommunication/ram:URIID')},
        }
        if seller:
            result['tax_number'] = tax_ids.get('FC', '')
            result['legal_info'] = d.text(node, 'ram:Description')
        return result

    lines = []
    for item in d.all(transaction, 'ram:IncludedSupplyChainTradeLineItem'):
        line_settlement = d.node(item, 'ram:SpecifiedLineTradeSettlement')
        price_node = d.node(item, 'ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice')
        price = d.text(price_node, 'ram:ChargeAmount')
        basis = d.text(price_node, 'ram:BasisQuantity')
        quantity = d.node(item, 'ram:SpecifiedLineTradeDelivery/ram:BilledQuantity')
        line = {
            'id': d.text(item, 'ram:AssociatedDocumentLineDocument/ram:LineID'),
            'note': d.text(item, 'ram:AssociatedDocumentLineDocument/ram:IncludedNote/ram:Content'),
            'name': d.text(item, 'ram:SpecifiedTradeProduct/ram:Name'),
            'description': d.text(item, 'ram:SpecifiedTradeProduct/ram:Description'),
            'item_id': d.text(item, 'ram:SpecifiedTradeProduct/ram:SellerAssignedID'),
            'buyer_item_id': d.text(item, 'ram:SpecifiedTradeProduct/ram:BuyerAssignedID'),
            'order_line': d.text(item, 'ram:SpecifiedLineTradeAgreement/ram:BuyerOrderReferencedDocument/ram:LineID'),
            'quantity': (quantity.text or '').strip() if quantity is not None else '',
            'unit': quantity.get('unitCode', '') if quantity is not None else '',
            'price': price,
            'price_basis_quantity': basis,
            'vat_category': d.text(line_settlement, 'ram:ApplicableTradeTax/ram:CategoryCode'),
            'vat_rate': d.text(line_settlement, 'ram:ApplicableTradeTax/ram:RateApplicablePercent'),
            'net': d.text(line_settlement, 'ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount'),
        }
        line['discount'] = _line_discount(
            [(d.text(a, 'ram:ActualAmount'), d.text(a, 'ram:CalculationPercent'), d.text(a, 'ram:Reason'))
             for a in d.all(line_settlement, 'ram:SpecifiedTradeAllowanceCharge')
             if d.text(a, 'ram:ChargeIndicator/udt:Indicator').lower() == 'false'])
        line['surcharge'] = _line_surcharge(
            [(d.text(a, 'ram:ActualAmount'), d.text(a, 'ram:Reason'))
             for a in d.all(line_settlement, 'ram:SpecifiedTradeAllowanceCharge')
             if d.text(a, 'ram:ChargeIndicator/udt:Indicator').lower() == 'true'])
        lines.append(line)

    allowances, charges = [], []
    for node in d.all(settlement, 'ram:SpecifiedTradeAllowanceCharge'):
        entry = {'amount': d.text(node, 'ram:ActualAmount'), 'percent': d.text(node, 'ram:CalculationPercent'),
                 'base': d.text(node, 'ram:BasisAmount'), 'reason': d.text(node, 'ram:Reason'),
                 'vat_category': d.text(node, 'ram:CategoryTradeTax/ram:CategoryCode'),
                 'vat_rate': d.text(node, 'ram:CategoryTradeTax/ram:RateApplicablePercent')}
        is_charge = d.text(node, 'ram:ChargeIndicator/udt:Indicator').lower() == 'true'
        (charges if is_charge else allowances).append(entry)

    means = d.node(settlement, 'ram:SpecifiedTradeSettlementPaymentMeans')
    terms = d.node(settlement, 'ram:SpecifiedTradePaymentTerms')
    payment = {
        'means': d.text(means, 'ram:TypeCode'),
        'info': d.text(means, 'ram:Information'),
        'iban': d.text(means, 'ram:PayeePartyCreditorFinancialAccount/ram:IBANID')
        or d.text(means, 'ram:PayeePartyCreditorFinancialAccount/ram:ProprietaryID'),
        'account_name': d.text(means, 'ram:PayeePartyCreditorFinancialAccount/ram:AccountName'),
        'bic': d.text(means, 'ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID'),
        'debtor_iban': d.text(means, 'ram:PayerPartyDebtorFinancialAccount/ram:IBANID'),
        'creditor_id': d.text(settlement, 'ram:CreditorReferenceID'),
        'mandate_id': d.text(terms, 'ram:DirectDebitMandateID'),
        'reference': d.text(settlement, 'ram:PaymentReference'),
    }
    _terms('\n'.join((t.text or '') for t in d.all(settlement, 'ram:SpecifiedTradePaymentTerms/ram:Description')),
           payment)

    sums = d.node(settlement, 'ram:SpecifiedTradeSettlementHeaderMonetarySummation')
    stated = {
        'line_total': d.text(sums, 'ram:LineTotalAmount'),
        'allowance_total': d.text(sums, 'ram:AllowanceTotalAmount'),
        'charge_total': d.text(sums, 'ram:ChargeTotalAmount'),
        'tax_basis_total': d.text(sums, 'ram:TaxBasisTotalAmount'),
        'tax_total': next((t.text.strip() for t in d.all(sums, 'ram:TaxTotalAmount')
                           if t.get('currencyID', '') in ('', d.text(settlement, 'ram:InvoiceCurrencyCode'))
                           and t.text), ''),
        'grand_total': d.text(sums, 'ram:GrandTotalAmount'),
        'prepaid': d.text(sums, 'ram:TotalPrepaidAmount'),
        'rounding': d.text(sums, 'ram:RoundingAmount'),
        'due_amount': d.text(sums, 'ram:DuePayableAmount'),
        'vat_groups': [{'category': d.text(tax, 'ram:CategoryCode'),
                        'rate': d.text(tax, 'ram:RateApplicablePercent'),
                        'basis': d.text(tax, 'ram:BasisAmount'),
                        'amount': d.text(tax, 'ram:CalculatedAmount'),
                        'exemption_reason': d.text(tax, 'ram:ExemptionReason'),
                        'exemption_code': d.text(tax, 'ram:ExemptionReasonCode')}
                       for tax in d.all(settlement, 'ram:ApplicableTradeTax')],
    }
    reasons = [group['exemption_reason'] for group in stated['vat_groups'] if group['exemption_reason']]
    ship_to = d.node(delivery, 'ram:ShipToTradeParty')
    ship_address = d.node(ship_to, 'ram:PostalTradeAddress')
    data = {
        'syntax': 'CII',
        'specification': d.text(root, 'rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID'),
        'number': d.text(document, 'ram:ID'),
        'type': d.text(document, 'ram:TypeCode'),
        'issue_date': _date(d.text(document, 'ram:IssueDateTime/udt:DateTimeString')),
        'due_date': _date(d.text(terms, 'ram:DueDateDateTime/udt:DateTimeString')),
        'delivery_date': _date(d.text(delivery, 'ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString')),
        'period': {'start': _date(d.text(settlement, 'ram:BillingSpecifiedPeriod/ram:StartDateTime/udt:DateTimeString')),
                   'end': _date(d.text(settlement, 'ram:BillingSpecifiedPeriod/ram:EndDateTime/udt:DateTimeString'))},
        'currency': d.text(settlement, 'ram:InvoiceCurrencyCode'),
        'buyer_reference': d.text(agreement, 'ram:BuyerReference'),
        'order_reference': d.text(agreement, 'ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID'),
        'contract_reference': d.text(agreement, 'ram:ContractReferencedDocument/ram:IssuerAssignedID'),
        'project_reference': d.text(agreement, 'ram:SpecifiedProcuringProject/ram:ID'),
        'preceding_invoice': {'number': d.text(settlement, 'ram:InvoiceReferencedDocument/ram:IssuerAssignedID'),
                              'date': _date(d.text(settlement, 'ram:InvoiceReferencedDocument/ram:FormattedIssueDateTime/qdt:DateTimeString'))},
        'note': [(n.text or '').strip() for n in d.all(document, 'ram:IncludedNote/ram:Content') if n.text],
        'seller': party(d.node(agreement, 'ram:SellerTradeParty'), seller=True),
        'buyer': party(d.node(agreement, 'ram:BuyerTradeParty'), seller=False),
        'delivery': {'name': d.text(ship_to, 'ram:Name'), 'street': d.text(ship_address, 'ram:LineOne'),
                     'postcode': d.text(ship_address, 'ram:PostcodeCode'),
                     'city': d.text(ship_address, 'ram:CityName'), 'country': d.text(ship_address, 'ram:CountryID')},
        'lines': lines,
        'allowances': allowances,
        'charges': charges,
        'vat_exemption_reason': reasons[0] if len(set(reasons)) == 1 else '',
        'payment': payment,
        'prepaid': stated['prepaid'],
        'rounding': stated['rounding'],
        'stated': stated,
    }
    return _clean(data)


def _read_ubl(root, credit):
    d = _Doc(UBL_NS)
    currency = d.text(root, 'cbc:DocumentCurrencyCode')

    def party(node, seller):
        node = d.node(node, 'cac:Party')
        if node is None:
            return {}
        tax_ids = {d.text(scheme, 'cac:TaxScheme/cbc:ID'): d.text(scheme, 'cbc:CompanyID')
                   for scheme in d.all(node, 'cac:PartyTaxScheme')}
        identifiers = [(item.get('schemeID', ''), (item.text or '').strip())
                       for item in d.all(node, 'cac:PartyIdentification/cbc:ID')]
        address = d.node(node, 'cac:PostalAddress')
        contact = d.node(node, 'cac:Contact')
        result = {
            'name': d.text(node, 'cac:PartyLegalEntity/cbc:RegistrationName') or d.text(node, 'cac:PartyName/cbc:Name'),
            'trading_name': d.text(node, 'cac:PartyName/cbc:Name'),
            'id': next((value for scheme, value in identifiers if scheme != 'SEPA'), ''),
            'register_id': d.text(node, 'cac:PartyLegalEntity/cbc:CompanyID'),
            'vat_id': tax_ids.get('VAT', ''),
            'street': d.text(address, 'cbc:StreetName'),
            'additional_street': ' '.join(filter(None, (d.text(address, 'cbc:AdditionalStreetName'),
                                                        d.text(address, 'cac:AddressLine/cbc:Line')))),
            'postcode': d.text(address, 'cbc:PostalZone'),
            'city': d.text(address, 'cbc:CityName'),
            'country': d.text(address, 'cac:Country/cbc:IdentificationCode'),
            'email': d.text(node, 'cbc:EndpointID'),
            'contact': {'name': d.text(contact, 'cbc:Name'), 'phone': d.text(contact, 'cbc:Telephone'),
                        'email': d.text(contact, 'cbc:ElectronicMail')},
        }
        if result['trading_name'] == result['name']:
            result['trading_name'] = ''
        if seller:
            result['tax_number'] = next((value for key, value in tax_ids.items() if key != 'VAT'), '')
            result['legal_info'] = d.text(node, 'cac:PartyLegalEntity/cbc:CompanyLegalForm')
            result['_creditor_id'] = next((value for scheme, value in identifiers if scheme == 'SEPA'), '')
        return result

    lines = []
    for item in d.all(root, 'cac:CreditNoteLine' if credit else 'cac:InvoiceLine'):
        quantity = d.node(item, 'cbc:CreditedQuantity' if credit else 'cbc:InvoicedQuantity')
        tax = d.node(item, 'cac:Item/cac:ClassifiedTaxCategory')
        line = {
            'id': d.text(item, 'cbc:ID'),
            'note': d.text(item, 'cbc:Note'),
            'name': d.text(item, 'cac:Item/cbc:Name'),
            'description': d.text(item, 'cac:Item/cbc:Description'),
            'item_id': d.text(item, 'cac:Item/cac:SellersItemIdentification/cbc:ID'),
            'buyer_item_id': d.text(item, 'cac:Item/cac:BuyersItemIdentification/cbc:ID'),
            'order_line': d.text(item, 'cac:OrderLineReference/cbc:LineID'),
            'quantity': (quantity.text or '').strip() if quantity is not None else '',
            'unit': quantity.get('unitCode', '') if quantity is not None else '',
            'price': d.text(item, 'cac:Price/cbc:PriceAmount'),
            'price_basis_quantity': d.text(item, 'cac:Price/cbc:BaseQuantity'),
            'vat_category': d.text(tax, 'cbc:ID'),
            'vat_rate': d.text(tax, 'cbc:Percent'),
            'net': d.text(item, 'cbc:LineExtensionAmount'),
        }
        line['discount'] = _line_discount(
            [(d.text(a, 'cbc:Amount'), d.text(a, 'cbc:MultiplierFactorNumeric'), d.text(a, 'cbc:AllowanceChargeReason'))
             for a in d.all(item, 'cac:AllowanceCharge') if d.text(a, 'cbc:ChargeIndicator').lower() == 'false'])
        line['surcharge'] = _line_surcharge(
            [(d.text(a, 'cbc:Amount'), d.text(a, 'cbc:AllowanceChargeReason'))
             for a in d.all(item, 'cac:AllowanceCharge') if d.text(a, 'cbc:ChargeIndicator').lower() == 'true'])
        lines.append(line)

    allowances, charges = [], []
    for node in d.all(root, 'cac:AllowanceCharge'):
        entry = {'amount': d.text(node, 'cbc:Amount'), 'percent': d.text(node, 'cbc:MultiplierFactorNumeric'),
                 'base': d.text(node, 'cbc:BaseAmount'), 'reason': d.text(node, 'cbc:AllowanceChargeReason'),
                 'vat_category': d.text(node, 'cac:TaxCategory/cbc:ID'),
                 'vat_rate': d.text(node, 'cac:TaxCategory/cbc:Percent')}
        (charges if d.text(node, 'cbc:ChargeIndicator').lower() == 'true' else allowances).append(entry)

    means = d.node(root, 'cac:PaymentMeans')
    seller = party(d.node(root, 'cac:AccountingSupplierParty'), seller=True)
    payment = {
        'means': d.text(means, 'cbc:PaymentMeansCode'),
        'info': d.attr(means, 'cbc:PaymentMeansCode', 'name') or d.text(means, 'cbc:InstructionNote'),
        'iban': d.text(means, 'cac:PayeeFinancialAccount/cbc:ID'),
        'account_name': d.text(means, 'cac:PayeeFinancialAccount/cbc:Name'),
        'bic': d.text(means, 'cac:PayeeFinancialAccount/cac:FinancialInstitutionBranch/cbc:ID'),
        'debtor_iban': d.text(means, 'cac:PaymentMandate/cac:PayerFinancialAccount/cbc:ID'),
        'mandate_id': d.text(means, 'cac:PaymentMandate/cbc:ID'),
        'creditor_id': seller.pop('_creditor_id', ''),
        'reference': d.text(means, 'cbc:PaymentID'),
    }
    _terms('\n'.join((n.text or '') for n in d.all(root, 'cac:PaymentTerms/cbc:Note')), payment)

    sums = d.node(root, 'cac:LegalMonetaryTotal')
    tax_total = next((t for t in d.all(root, 'cac:TaxTotal')
                      if d.attr(t, 'cbc:TaxAmount', 'currencyID') in ('', currency)), None)
    stated = {
        'line_total': d.text(sums, 'cbc:LineExtensionAmount'),
        'allowance_total': d.text(sums, 'cbc:AllowanceTotalAmount'),
        'charge_total': d.text(sums, 'cbc:ChargeTotalAmount'),
        'tax_basis_total': d.text(sums, 'cbc:TaxExclusiveAmount'),
        'tax_total': d.text(tax_total, 'cbc:TaxAmount'),
        'grand_total': d.text(sums, 'cbc:TaxInclusiveAmount'),
        'prepaid': d.text(sums, 'cbc:PrepaidAmount'),
        'rounding': d.text(sums, 'cbc:PayableRoundingAmount'),
        'due_amount': d.text(sums, 'cbc:PayableAmount'),
        'vat_groups': [{'category': d.text(sub, 'cac:TaxCategory/cbc:ID'),
                        'rate': d.text(sub, 'cac:TaxCategory/cbc:Percent'),
                        'basis': d.text(sub, 'cbc:TaxableAmount'),
                        'amount': d.text(sub, 'cbc:TaxAmount'),
                        'exemption_reason': d.text(sub, 'cac:TaxCategory/cbc:TaxExemptionReason'),
                        'exemption_code': d.text(sub, 'cac:TaxCategory/cbc:TaxExemptionReasonCode')}
                       for sub in d.all(tax_total, 'cac:TaxSubtotal')],
    }
    reasons = [group['exemption_reason'] for group in stated['vat_groups'] if group['exemption_reason']]
    delivery = d.node(root, 'cac:Delivery')
    ship_address = d.node(delivery, 'cac:DeliveryLocation/cac:Address')
    project = d.text(root, 'cac:ProjectReference/cbc:ID') or next(
        (d.text(ref, 'cbc:ID') for ref in d.all(root, 'cac:AdditionalDocumentReference')
         if d.text(ref, 'cbc:DocumentTypeCode') == '50'), '')
    data = {
        'syntax': 'UBL',
        'specification': d.text(root, 'cbc:CustomizationID'),
        'number': d.text(root, 'cbc:ID'),
        'type': d.text(root, 'cbc:CreditNoteTypeCode' if credit else 'cbc:InvoiceTypeCode'),
        'issue_date': _date(d.text(root, 'cbc:IssueDate')),
        'due_date': _date(d.text(root, 'cbc:DueDate') or d.text(means, 'cbc:PaymentDueDate')),
        'delivery_date': _date(d.text(delivery, 'cbc:ActualDeliveryDate')),
        'period': {'start': _date(d.text(root, 'cac:InvoicePeriod/cbc:StartDate')),
                   'end': _date(d.text(root, 'cac:InvoicePeriod/cbc:EndDate'))},
        'currency': currency,
        'buyer_reference': d.text(root, 'cbc:BuyerReference'),
        'order_reference': d.text(root, 'cac:OrderReference/cbc:ID'),
        'contract_reference': d.text(root, 'cac:ContractDocumentReference/cbc:ID'),
        'project_reference': project,
        'preceding_invoice': {'number': d.text(root, 'cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID'),
                              'date': _date(d.text(root, 'cac:BillingReference/cac:InvoiceDocumentReference/cbc:IssueDate'))},
        'note': [(n.text or '').strip() for n in d.all(root, 'cbc:Note') if n.text],
        'seller': seller,
        'buyer': party(d.node(root, 'cac:AccountingCustomerParty'), seller=False),
        'delivery': {'name': d.text(delivery, 'cac:DeliveryParty/cac:PartyName/cbc:Name'),
                     'street': d.text(ship_address, 'cbc:StreetName'),
                     'postcode': d.text(ship_address, 'cbc:PostalZone'),
                     'city': d.text(ship_address, 'cbc:CityName'),
                     'country': d.text(ship_address, 'cac:Country/cbc:IdentificationCode')},
        'lines': lines,
        'allowances': allowances,
        'charges': charges,
        'vat_exemption_reason': reasons[0] if len(set(reasons)) == 1 else '',
        'payment': payment,
        'prepaid': stated['prepaid'],
        'rounding': stated['rounding'],
        'stated': stated,
    }
    return _clean(data)
