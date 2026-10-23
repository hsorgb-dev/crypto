"""Invoice data model: reads the JSON input and computes every amount the way EN 16931 defines it.

All amounts are Decimal and rounded half-up to cents, so the XML, the PDF and the official
validators all see the same numbers."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import codes

CENT = Decimal('0.01')
ZERO = Decimal('0')
HUNDRED = Decimal('100')


def money(value):
    return value.quantize(CENT, ROUND_HALF_UP)


def plain(value):
    """Decimal as text without exponent and without needless trailing zeros (19.00 -> 19)."""
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


def amount_text(value):
    return format(money(value), 'f')


def price_text(value):
    """Unit prices keep up to four decimals but always show at least two (249.9 -> 249.90)."""
    return format(value if value.as_tuple().exponent < -2 else money(value), 'f')


@dataclass
class Issue:
    level: str          # 'fehler' blocks the invoice, 'hinweis' does not
    field: str          # path in the input, e.g. 'seller.vat_id' or 'lines[2].price'
    message: str        # German, written for the person or the AI that filled in the data
    rule: str = ''      # EN 16931 / XRechnung rule, e.g. 'BR-DE-15'

    def __str__(self):
        rule = f' [{self.rule}]' if self.rule else ''
        return f'{self.level.upper()} {self.field}: {self.message}{rule}'

    def as_dict(self):
        return {'level': self.level, 'field': self.field, 'message': self.message, 'rule': self.rule}


class InvoiceError(ValueError):
    """The input cannot become a valid e-invoice. `issues` lists every problem found."""

    def __init__(self, issues):
        self.issues = list(issues)
        super().__init__('\n'.join(str(issue) for issue in self.issues))


@dataclass
class Contact:
    name: str = ''
    phone: str = ''
    email: str = ''


@dataclass
class Address:
    street: str = ''
    additional_street: str = ''
    postcode: str = ''
    city: str = ''
    country: str = 'DE'


@dataclass
class Party:
    name: str = ''
    address: Address = field(default_factory=Address)
    trading_name: str = ''       # BT-28 / BT-45
    id: str = ''                 # BT-29 / BT-46, e.g. supplier or customer number
    register_id: str = ''        # BT-30 / BT-47, e.g. "HRB 12345, Amtsgericht Berlin"
    vat_id: str = ''             # BT-31 / BT-48
    tax_number: str = ''         # BT-32 (seller only)
    legal_info: str = ''         # BT-33 (seller only)
    email: str = ''              # BT-34 / BT-49 electronic address
    contact: Contact = field(default_factory=Contact)
    small_business: bool = False  # Kleinunternehmer nach § 19 UStG (seller only)


@dataclass
class Line:
    id: str
    name: str
    quantity: Decimal
    unit: str
    price: Decimal               # BT-146 net price per base quantity
    vat_category: str
    vat_rate: Decimal | None     # None for category O
    description: str = ''
    item_id: str = ''            # BT-155 seller's item number
    buyer_item_id: str = ''      # BT-156
    note: str = ''               # BT-127
    order_line: str = ''         # BT-132
    base_quantity: Decimal = Decimal('1')  # BT-149, e.g. price per 100 pieces
    discount_percent: Decimal | None = None
    discount_amount: Decimal = ZERO  # BT-136
    discount_reason: str = ''
    charge_amount: Decimal = ZERO  # BT-141, surcharge on this line
    charge_reason: str = ''
    gross: Decimal = ZERO        # quantity x price, base of the discount (BT-137)
    net: Decimal = ZERO          # BT-131


@dataclass
class AllowanceCharge:
    charge: bool                 # False = Nachlass (BG-20), True = Zuschlag (BG-21)
    amount: Decimal
    reason: str
    vat_category: str
    vat_rate: Decimal | None
    percent: Decimal | None = None
    base: Decimal | None = None


@dataclass
class VatGroup:
    category: str
    rate: Decimal | None
    basis: Decimal = ZERO
    amount: Decimal = ZERO
    exemption_reason: str = ''
    exemption_code: str = ''


@dataclass
class Payment:
    means: str = '58'
    iban: str = ''
    bic: str = ''
    account_name: str = ''
    reference: str = ''          # BT-83 Verwendungszweck
    terms: str = ''              # BT-20
    terms_days: int | None = None
    discount_days: int | None = None
    discount_percent: Decimal | None = None
    mandate_id: str = ''         # BT-89
    creditor_id: str = ''        # BT-90
    debtor_iban: str = ''        # BT-91
    info: str = ''               # BT-82


@dataclass
class Invoice:
    number: str = ''
    type_code: str = '380'
    issue_date: date | None = None
    due_date: date | None = None
    delivery_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    currency: str = 'EUR'
    buyer_reference: str = ''    # BT-10, Leitweg-ID for public buyers
    order_reference: str = ''    # BT-13
    contract_reference: str = ''  # BT-12
    project_reference: str = ''  # BT-11
    preceding_number: str = ''   # BT-25
    preceding_date: date | None = None
    notes: list = field(default_factory=list)
    seller: Party = field(default_factory=Party)
    buyer: Party = field(default_factory=Party)
    delivery_name: str = ''
    delivery_address: Address | None = None
    lines: list = field(default_factory=list)
    allowances: list = field(default_factory=list)  # AllowanceCharge with charge=False
    charges: list = field(default_factory=list)     # AllowanceCharge with charge=True
    exemption_reason: str = ''
    payment: Payment = field(default_factory=Payment)
    prepaid: Decimal = ZERO      # BT-113
    rounding: Decimal = ZERO     # BT-114
    # computed by compute()
    vat_groups: list = field(default_factory=list)
    line_total: Decimal = ZERO
    allowance_total: Decimal = ZERO
    charge_total: Decimal = ZERO
    tax_basis_total: Decimal = ZERO
    tax_total: Decimal = ZERO
    grand_total: Decimal = ZERO
    due_amount: Decimal = ZERO

    @property
    def payment_terms_text(self):
        """BT-20 as written into the XML: the free text plus the XRechnung Skonto line (BR-DE-18)."""
        payment = self.payment
        text = payment.terms.strip()
        if not text and self.due_amount > 0:
            if self.is_credit_note:
                text = 'Der Betrag wird Ihnen gutgeschrieben.'
            elif self.due_date:
                text = f'Zahlbar ohne Abzug bis zum {self.due_date:%d.%m.%Y}.'
            else:
                text = 'Zahlbar sofort ohne Abzug.'
        if payment.discount_days and payment.discount_percent:
            percent = format(payment.discount_percent.quantize(CENT, ROUND_HALF_UP), 'f')
            human = (f'Bei Zahlung innerhalb von {payment.discount_days} Tagen '
                     f'{percent.replace(".", ",")} % Skonto.')
            text = ' '.join(part for part in (text, human) if part)
            text += f'\n#SKONTO#TAGE={payment.discount_days}#PROZENT={percent}#\n'
        return text

    @property
    def is_credit_note(self):
        return self.type_code == '381'


# ---------------------------------------------------------------- reading the input

class _Reader:
    """Turns the loose JSON input into typed values and collects every problem instead of stopping."""

    def __init__(self):
        self.issues = []

    def error(self, path, message, rule=''):
        self.issues.append(Issue('fehler', path, message, rule))

    def text(self, data, key, path, default=''):
        value = data.get(key, default) if isinstance(data, dict) else default
        if value is None:
            return default
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return str(value)
        if not isinstance(value, str):
            self.error(path, 'Text erwartet.')
            return default
        return value.strip()

    def number(self, data, key, path, default=None):
        value = data.get(key) if isinstance(data, dict) else None
        if value is None or value == '':
            return default
        try:
            return parse_decimal(value)
        except (InvalidOperation, ValueError, TypeError):
            self.error(path, f'Zahl erwartet, erhalten: {value!r}.')
            return default

    def integer(self, data, key, path):
        value = self.number(data, key, path)
        if value is None:
            return None
        if value != value.to_integral_value():
            self.error(path, 'Ganze Zahl erwartet.')
            return None
        return int(value)

    def date(self, data, key, path):
        value = data.get(key) if isinstance(data, dict) else None
        if value in (None, ''):
            return None
        if isinstance(value, date):
            return value
        try:
            return parse_date(str(value))
        except ValueError:
            self.error(path, f'Datum im Format JJJJ-MM-TT erwartet, erhalten: {value!r}.')
            return None

    def dict(self, data, key, path):
        value = data.get(key) if isinstance(data, dict) else None
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.error(path, 'Objekt erwartet.')
            return {}
        return value

    def list(self, data, key, path):
        value = data.get(key) if isinstance(data, dict) else None
        if value is None:
            return []
        if not isinstance(value, list):
            self.error(path, 'Liste erwartet.')
            return []
        return value


def parse_decimal(value):
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    text = str(value).strip().replace(' ', '').replace('€', '').replace('%', '')
    if ',' in text and '.' in text:
        text = text.replace('.', '').replace(',', '.')   # German "1.234,56"
    elif ',' in text:
        text = text.replace(',', '.')
    result = Decimal(text)
    if not result.is_finite():
        raise ValueError(value)
    return result


def parse_date(text):
    text = text.strip()
    if len(text) == 10 and text[2] == '.' and text[5] == '.':   # German 31.12.2026
        return date(int(text[6:]), int(text[3:5]), int(text[:2]))
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return date.fromisoformat(text[:10])


def _address(reader, data, path):
    country = reader.text(data, 'country', f'{path}.country', 'DE').upper() or 'DE'
    return Address(street=reader.text(data, 'street', f'{path}.street'),
                   additional_street=reader.text(data, 'additional_street', f'{path}.additional_street'),
                   postcode=reader.text(data, 'postcode', f'{path}.postcode'),
                   city=reader.text(data, 'city', f'{path}.city'),
                   country=codes.COUNTRY_ALIASES.get(country.lower(), country))


def _party(reader, data, path):
    contact = reader.dict(data, 'contact', f'{path}.contact')
    return Party(name=reader.text(data, 'name', f'{path}.name'),
                 address=_address(reader, data, path),
                 trading_name=reader.text(data, 'trading_name', f'{path}.trading_name'),
                 id=reader.text(data, 'id', f'{path}.id'),
                 register_id=reader.text(data, 'register_id', f'{path}.register_id'),
                 vat_id=reader.text(data, 'vat_id', f'{path}.vat_id').replace(' ', '').upper(),
                 tax_number=reader.text(data, 'tax_number', f'{path}.tax_number'),
                 legal_info=reader.text(data, 'legal_info', f'{path}.legal_info'),
                 email=reader.text(data, 'email', f'{path}.email'),
                 contact=Contact(name=reader.text(contact, 'name', f'{path}.contact.name'),
                                 phone=reader.text(contact, 'phone', f'{path}.contact.phone'),
                                 email=reader.text(contact, 'email', f'{path}.contact.email')),
                 small_business=bool(data.get('small_business', False)))


def _vat(reader, data, path, seller):
    """VAT category and rate of a line, allowance or charge. Defaults: 19 % (S), or E for Kleinunternehmer."""
    default_category = 'E' if seller.small_business else 'S'
    category = reader.text(data, 'vat_category', f'{path}.vat_category', default_category).upper()
    category = codes.VAT_CATEGORY_ALIASES.get(category.lower(), category) or default_category
    rate = reader.number(data, 'vat_rate', f'{path}.vat_rate')
    if category not in codes.ALL_VAT_CATEGORIES:
        reader.error(f'{path}.vat_category',
                     f'Unbekannte USt-Kategorie {category!r}. Erlaubt: {", ".join(codes.VAT_CATEGORIES)}.')
        category = default_category
    if category == 'S':
        rate = Decimal('19') if rate is None else rate
    elif category == 'O':
        rate = None
    else:
        rate = ZERO if rate is None else rate
    return category, rate


def parse_invoice(data):
    """Read the input dict. Returns (Invoice, issues); amounts are already computed."""
    reader = _Reader()
    if not isinstance(data, dict):
        return Invoice(), [Issue('fehler', '', 'Die Rechnungsdaten müssen ein JSON-Objekt sein.')]
    invoice = Invoice()
    invoice.number = reader.text(data, 'number', 'number')
    type_code = reader.text(data, 'type', 'type', '380')
    invoice.type_code = codes.TYPE_ALIASES.get(type_code.lower(), type_code)
    invoice.issue_date = reader.date(data, 'issue_date', 'issue_date')
    invoice.due_date = reader.date(data, 'due_date', 'due_date')
    invoice.delivery_date = reader.date(data, 'delivery_date', 'delivery_date')
    period = reader.dict(data, 'period', 'period')
    invoice.period_start = reader.date(period, 'start', 'period.start')
    invoice.period_end = reader.date(period, 'end', 'period.end')
    invoice.currency = reader.text(data, 'currency', 'currency', 'EUR').upper() or 'EUR'
    invoice.buyer_reference = reader.text(data, 'buyer_reference', 'buyer_reference')
    invoice.order_reference = reader.text(data, 'order_reference', 'order_reference')
    invoice.contract_reference = reader.text(data, 'contract_reference', 'contract_reference')
    invoice.project_reference = reader.text(data, 'project_reference', 'project_reference')
    preceding = reader.dict(data, 'preceding_invoice', 'preceding_invoice')
    invoice.preceding_number = reader.text(preceding, 'number', 'preceding_invoice.number')
    invoice.preceding_date = reader.date(preceding, 'date', 'preceding_invoice.date')
    note = data.get('note')
    notes = note if isinstance(note, list) else [note]
    invoice.notes = [str(item).strip() for item in notes if item not in (None, '') and str(item).strip()]
    invoice.seller = _party(reader, reader.dict(data, 'seller', 'seller'), 'seller')
    invoice.buyer = _party(reader, reader.dict(data, 'buyer', 'buyer'), 'buyer')
    delivery = reader.dict(data, 'delivery', 'delivery')
    if any(delivery.get(key) for key in ('name', 'street', 'postcode', 'city')):
        invoice.delivery_name = reader.text(delivery, 'name', 'delivery.name')
        invoice.delivery_address = _address(reader, delivery, 'delivery')
    invoice.exemption_reason = reader.text(data, 'vat_exemption_reason', 'vat_exemption_reason')

    for index, raw in enumerate(reader.list(data, 'lines', 'lines')):
        path = f'lines[{index}]'
        if not isinstance(raw, dict):
            reader.error(path, 'Position muss ein Objekt sein.')
            continue
        category, rate = _vat(reader, raw, path, invoice.seller)
        unit = reader.text(raw, 'unit', f'{path}.unit', 'C62') or 'C62'
        quantity = reader.number(raw, 'quantity', f'{path}.quantity', Decimal('1'))
        price = reader.number(raw, 'price', f'{path}.price')
        if price is None:
            reader.error(f'{path}.price', 'Einzelpreis (netto) fehlt.', 'BR-26')
            price = ZERO
        discount = reader.dict(raw, 'discount', f'{path}.discount')
        surcharge = reader.dict(raw, 'surcharge', f'{path}.surcharge')
        invoice.lines.append(Line(
            id=reader.text(raw, 'id', f'{path}.id') or str(index + 1),
            name=reader.text(raw, 'name', f'{path}.name'),
            quantity=quantity,
            unit=codes.UNIT_ALIASES.get(unit.lower(), unit),
            price=price,
            vat_category=category,
            vat_rate=rate,
            description=reader.text(raw, 'description', f'{path}.description'),
            item_id=reader.text(raw, 'item_id', f'{path}.item_id'),
            buyer_item_id=reader.text(raw, 'buyer_item_id', f'{path}.buyer_item_id'),
            note=reader.text(raw, 'note', f'{path}.note'),
            order_line=reader.text(raw, 'order_line', f'{path}.order_line'),
            base_quantity=reader.number(raw, 'price_basis_quantity', f'{path}.price_basis_quantity', Decimal('1')),
            discount_percent=reader.number(discount, 'percent', f'{path}.discount.percent'),
            discount_amount=reader.number(discount, 'amount', f'{path}.discount.amount', ZERO),
            discount_reason=reader.text(discount, 'reason', f'{path}.discount.reason') or 'Rabatt',
            charge_amount=reader.number(surcharge, 'amount', f'{path}.surcharge.amount', ZERO),
            charge_reason=reader.text(surcharge, 'reason', f'{path}.surcharge.reason') or 'Zuschlag'))

    for key, charge in (('allowances', False), ('charges', True)):
        target = invoice.charges if charge else invoice.allowances
        for index, raw in enumerate(reader.list(data, key, key)):
            path = f'{key}[{index}]'
            if not isinstance(raw, dict):
                reader.error(path, 'Objekt erwartet.')
                continue
            category, rate = _vat(reader, raw, path, invoice.seller)
            target.append(AllowanceCharge(
                charge=charge,
                amount=reader.number(raw, 'amount', f'{path}.amount'),
                reason=reader.text(raw, 'reason', f'{path}.reason') or ('Zuschlag' if charge else 'Nachlass'),
                vat_category=category, vat_rate=rate,
                percent=reader.number(raw, 'percent', f'{path}.percent'),
                base=reader.number(raw, 'base', f'{path}.base')))

    payment = reader.dict(data, 'payment', 'payment')
    discount = reader.dict(payment, 'discount', 'payment.discount')
    means = reader.text(payment, 'means', 'payment.means', '58') or '58'
    invoice.payment = Payment(
        means=codes.PAYMENT_ALIASES.get(means.lower(), means),
        iban=reader.text(payment, 'iban', 'payment.iban').replace(' ', '').upper(),
        bic=reader.text(payment, 'bic', 'payment.bic').replace(' ', '').upper(),
        account_name=reader.text(payment, 'account_name', 'payment.account_name'),
        reference=reader.text(payment, 'reference', 'payment.reference'),
        terms=reader.text(payment, 'terms', 'payment.terms'),
        terms_days=reader.integer(payment, 'terms_days', 'payment.terms_days'),
        discount_days=reader.integer(discount, 'days', 'payment.discount.days'),
        discount_percent=reader.number(discount, 'percent', 'payment.discount.percent'),
        mandate_id=reader.text(payment, 'mandate_id', 'payment.mandate_id'),
        creditor_id=reader.text(payment, 'creditor_id', 'payment.creditor_id'),
        debtor_iban=reader.text(payment, 'debtor_iban', 'payment.debtor_iban').replace(' ', '').upper(),
        info=reader.text(payment, 'info', 'payment.info'))
    invoice.prepaid = reader.number(data, 'prepaid', 'prepaid', ZERO)
    invoice.rounding = reader.number(data, 'rounding', 'rounding', ZERO)

    if invoice.due_date is None and invoice.payment.terms_days is not None and invoice.issue_date:
        invoice.due_date = invoice.issue_date + timedelta(days=invoice.payment.terms_days)
    if not invoice.payment.reference:
        invoice.payment.reference = invoice.number
    if not invoice.payment.account_name and invoice.payment.iban:
        invoice.payment.account_name = invoice.seller.name
    if invoice.seller.small_business and not invoice.exemption_reason:
        invoice.exemption_reason = codes.SMALL_BUSINESS_REASON

    compute(invoice, reader)
    if invoice.delivery_address is None and any(group.category == 'K' for group in invoice.vat_groups):
        # BR-IC-12: an intra-community supply needs the deliver-to country; default is the buyer's address
        invoice.delivery_name = invoice.buyer.name
        invoice.delivery_address = invoice.buyer.address
    return invoice, reader.issues


def compute(invoice, reader=None):
    """Line nets, VAT breakdown and document totals (BR-CO-10 ... BR-CO-16)."""
    for line in invoice.lines:
        line.gross = money(line.quantity * line.price / (line.base_quantity or Decimal('1')))
        if line.discount_percent is not None:
            line.discount_amount = money(line.gross * line.discount_percent / HUNDRED)
        line.discount_amount = money(line.discount_amount or ZERO)
        line.charge_amount = money(line.charge_amount or ZERO)
        line.net = line.gross - line.discount_amount + line.charge_amount

    groups = {}

    def group(category, rate):
        key = (category, rate)
        if key not in groups:
            groups[key] = VatGroup(category, rate)
        return groups[key]

    for line in invoice.lines:
        group(line.vat_category, line.vat_rate).basis += line.net
    for item in invoice.allowances + invoice.charges:
        if item.amount is None and item.percent is not None:
            base = item.base
            if base is None:
                base = sum((line.net for line in invoice.lines
                            if (line.vat_category, line.vat_rate) == (item.vat_category, item.vat_rate)), ZERO)
            item.base = money(base)
            item.amount = money(base * item.percent / HUNDRED)
        if item.amount is None:
            if reader:
                reader.error('allowances/charges', f'Betrag oder Prozentsatz fehlt bei „{item.reason}“.')
            item.amount = ZERO
        item.amount = money(item.amount)
        group(item.vat_category, item.vat_rate).basis += -item.amount if not item.charge else item.amount

    for vat in groups.values():
        vat.basis = money(vat.basis)
        taxed = vat.category in ('S', 'L', 'M') and vat.rate
        vat.amount = money(vat.basis * vat.rate / HUNDRED) if taxed else ZERO
        if vat.category not in ('S', 'Z', 'L', 'M'):
            code, reason = codes.EXEMPTIONS.get(vat.category, ('', ''))
            vat.exemption_code = code
            vat.exemption_reason = invoice.exemption_reason or reason

    invoice.vat_groups = sorted(groups.values(), key=lambda g: (codes.ALL_VAT_CATEGORIES.index(g.category),
                                                                -(g.rate or ZERO)))
    invoice.line_total = money(sum((line.net for line in invoice.lines), ZERO))
    invoice.allowance_total = money(sum((item.amount for item in invoice.allowances), ZERO))
    invoice.charge_total = money(sum((item.amount for item in invoice.charges), ZERO))
    invoice.tax_basis_total = invoice.line_total - invoice.allowance_total + invoice.charge_total
    invoice.tax_total = money(sum((vat.amount for vat in invoice.vat_groups), ZERO))
    invoice.grand_total = invoice.tax_basis_total + invoice.tax_total
    invoice.prepaid = money(invoice.prepaid)
    invoice.rounding = money(invoice.rounding)
    invoice.due_amount = invoice.grand_total - invoice.prepaid + invoice.rounding
    return invoice
