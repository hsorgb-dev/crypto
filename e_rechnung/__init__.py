"""E-Rechnung: XRechnung 3.0 (CII, UBL) and ZUGFeRD 2.3 / Factur-X (PDF/A-3) from one JSON input.

    from e_rechnung import create
    result = create(data, 'xrechnung-cii')      # or 'xrechnung-ubl', 'zugferd', 'zugferd-xrechnung'
    Path(result.filename).write_bytes(result.content)

The input format is described in schema.json (also the tool definition for the company AI)."""
from dataclasses import dataclass, field

from . import checks, cii, codes, ubl
from .model import Invoice, InvoiceError, Issue, parse_invoice

FORMATS = {
    'xrechnung-cii': ('XRechnung 3.0 (UN/CEFACT CII)', 'xml', 'application/xml', 'xrechnung'),
    'xrechnung-ubl': ('XRechnung 3.0 (UBL 2.1)', 'xml', 'application/xml', 'xrechnung'),
    'zugferd': ('ZUGFeRD 2.3 / Factur-X, Profil EN 16931 (PDF/A-3)', 'pdf', 'application/pdf', 'en16931'),
    'zugferd-xrechnung': ('ZUGFeRD 2.3, Profil XRECHNUNG (PDF/A-3)', 'pdf', 'application/pdf', 'xrechnung'),
    'pdf': ('Nur Sichtbeleg als PDF (keine E-Rechnung)', 'pdf', 'application/pdf', 'en16931'),
}


@dataclass
class Result:
    content: bytes
    filename: str
    mime: str
    format: str
    invoice: Invoice
    warnings: list = field(default_factory=list)   # Issue objects with level 'hinweis'


def validate(data, format='xrechnung-cii'):
    """All findings (errors and hints) for the input, without creating anything."""
    profile = FORMATS[_format(format)][3]
    invoice, issues = parse_invoice(data)
    return invoice, issues + checks.check(invoice, profile)


def create(data, format='xrechnung-cii'):
    """Build the e-invoice. Raises InvoiceError with every finding when the data is not sufficient."""
    format = _format(format)
    invoice, issues = validate(data, format)
    if checks.errors(issues):
        raise InvoiceError(issues)
    if format == 'xrechnung-cii':
        content = cii.build(invoice, codes.XRECHNUNG_ID)
    elif format == 'xrechnung-ubl':
        content = ubl.build(invoice, codes.XRECHNUNG_ID)
    else:
        from . import pdf   # needs reportlab and pypdf, only loaded for PDF output
        if format == 'pdf':
            content = pdf.visual(invoice)
        else:
            guideline = codes.XRECHNUNG_ID if format == 'zugferd-xrechnung' else codes.EN16931_ID
            content = pdf.zugferd(invoice, cii.build(invoice, guideline),
                                  'XRECHNUNG' if format == 'zugferd-xrechnung' else 'EN 16931')
    _, extension, mime, _ = FORMATS[format]
    return Result(content=content, filename=f'{safe_filename(invoice.number)}.{extension}', mime=mime,
                  format=format, invoice=invoice, warnings=[i for i in issues if i.level != 'fehler'])


def totals(invoice):
    """The computed amounts as strings, for APIs and the AI (exact decimals, no float rounding)."""
    return {
        'currency': invoice.currency,
        'line_total': str(invoice.line_total),
        'allowance_total': str(invoice.allowance_total),
        'charge_total': str(invoice.charge_total),
        'tax_basis_total': str(invoice.tax_basis_total),
        'tax_total': str(invoice.tax_total),
        'grand_total': str(invoice.grand_total),
        'prepaid': str(invoice.prepaid),
        'due_amount': str(invoice.due_amount),
        'due_date': invoice.due_date.isoformat() if invoice.due_date else None,
        'vat': [{'category': group.category, 'rate': None if group.rate is None else str(group.rate),
                 'basis': str(group.basis), 'amount': str(group.amount)} for group in invoice.vat_groups],
        'lines': [{'id': line.id, 'net': str(line.net)} for line in invoice.lines],
    }


def safe_filename(text):
    cleaned = ''.join(char if char.isalnum() or char in '-_.' else '_' for char in text).strip('._')
    return cleaned or 'rechnung'


def _format(format):
    key = (format or 'xrechnung-cii').lower().replace('_', '-')
    key = {'xrechnung': 'xrechnung-cii', 'cii': 'xrechnung-cii', 'ubl': 'xrechnung-ubl',
           'factur-x': 'zugferd', 'facturx': 'zugferd'}.get(key, key)
    if key not in FORMATS:
        raise InvoiceError([Issue('fehler', 'format', f'Unbekanntes Format {format!r}. Erlaubt: '
                                  + ', '.join(FORMATS) + '.')])
    return key


__all__ = ['FORMATS', 'Invoice', 'InvoiceError', 'Issue', 'Result', 'create', 'totals', 'validate']
