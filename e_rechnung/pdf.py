"""Readable invoice PDF (reportlab) and ZUGFeRD / Factur-X: PDF/A-3b with the CII XML embedded (pypdf).

PDF/A-3 needs embedded fonts, an sRGB output intent and XMP metadata that matches the document
info. The fonts come from the system (Liberation Sans, DejaVu Sans, Arial) or, as a fallback,
the Vera font that ships with reportlab; the sRGB profile is built by Pillow."""
import io
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (BaseDocTemplate, Frame, NextPageTemplate, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

from . import codes
from .fmt import de_amount, de_date, de_price, de_quantity, de_rate, iban_groups

PRODUCER = 'e_rechnung (Python)'
FONT_CANDIDATES = [
    ('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
     '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
    ('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
     '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'),
    ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    ('/System/Library/Fonts/Supplemental/Arial.ttf', '/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
    ('/Library/Fonts/Arial.ttf', '/Library/Fonts/Arial Bold.ttf'),
    ('C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/arialbd.ttf'),
]
GREY = colors.HexColor('#555555')
LINE = colors.HexColor('#BBBBBB')
SHADE = colors.HexColor('#EEF1F4')
LEFT, RIGHT, WIDTH = 20 * mm, A4[0] - 20 * mm, A4[0] - 40 * mm


def _fonts():
    """Register a regular and a bold TrueType font (always embedded, as PDF/A requires)."""
    if 'ER-Regular' in pdfmetrics.getRegisteredFontNames():
        return 'ER-Regular', 'ER-Bold'
    candidates = list(FONT_CANDIDATES)
    if os.environ.get('E_RECHNUNG_FONT'):
        candidates.insert(0, (os.environ['E_RECHNUNG_FONT'],
                              os.environ.get('E_RECHNUNG_FONT_BOLD', os.environ['E_RECHNUNG_FONT'])))
    import reportlab
    bundled = Path(reportlab.__file__).parent / 'fonts'
    candidates.append((str(bundled / 'Vera.ttf'), str(bundled / 'VeraBd.ttf')))
    for regular, bold in candidates:
        if Path(regular).is_file() and Path(bold).is_file():
            pdfmetrics.registerFont(TTFont('ER-Regular', regular))
            pdfmetrics.registerFont(TTFont('ER-Bold', bold))
            return 'ER-Regular', 'ER-Bold'
    raise RuntimeError('Keine TrueType-Schrift gefunden. E_RECHNUNG_FONT auf eine .ttf-Datei setzen.')


class _NumberedCanvas(pdf_canvas.Canvas):
    """Writes "Seite x von y" once the page count is known."""

    def __init__(self, *args, **kwargs):
        # start with the embedded font, otherwise reportlab selects the non-embedded Helvetica on every page
        kwargs['initialFontName'] = kwargs.get('initialFontName') or 'ER-Regular'
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont(self._er_font, 7.5)
            self.setFillColor(GREY)
            self.drawRightString(RIGHT, 10 * mm, f'Seite {self._pageNumber} von {total}')
            super().showPage()
        super().save()


def visual(invoice, embedded_note=''):
    """The human-readable invoice as PDF bytes (fonts embedded, no transparency)."""
    regular, bold = _fonts()
    currency = invoice.currency
    seller, buyer, payment = invoice.seller, invoice.buyer, invoice.payment
    title = f'{codes.TYPE_TITLES.get(invoice.type_code, "Rechnung")} {invoice.number}'

    style = ParagraphStyle('body', fontName=regular, fontSize=9.5, leading=12.5)
    small = ParagraphStyle('small', parent=style, fontSize=8, leading=10, textColor=GREY)
    cell = ParagraphStyle('cell', parent=style, fontSize=8.5, leading=10.5)
    cell_right = ParagraphStyle('cell_right', parent=cell, alignment=TA_RIGHT)
    heading = ParagraphStyle('heading', parent=style, fontName=bold, fontSize=15, leading=19, spaceAfter=4)

    def p(text, st=style):
        return Paragraph(text, st)

    info = [('Rechnungsnummer', invoice.number), ('Rechnungsdatum', de_date(invoice.issue_date))]
    if invoice.delivery_date:
        info.append(('Leistungsdatum', de_date(invoice.delivery_date)))
    if invoice.period_start or invoice.period_end:
        info.append(('Leistungszeitraum', f'{de_date(invoice.period_start)} – {de_date(invoice.period_end)}'))
    for label, value in (('Kundennummer', buyer.id), ('Ihre Bestellung', invoice.order_reference),
                         ('Vertrag', invoice.contract_reference), ('Projekt', invoice.project_reference),
                         ('Käuferreferenz', invoice.buyer_reference), ('Ihre USt-IdNr.', buyer.vat_id)):
        if value:
            info.append((label, value))
    if invoice.due_date and invoice.due_amount > 0:
        info.append(('Fällig am', de_date(invoice.due_date)))
    header_height = max(92 * mm, 50 * mm + len(info) * 11.5 + 12 * mm)

    def first_page(canvas, doc):
        canvas.saveState()
        top = A4[1]
        canvas.setFont(bold, 13)
        canvas.drawRightString(RIGHT, top - 18 * mm, seller.trading_name or seller.name)
        canvas.setFont(regular, 8.5)
        canvas.setFillColor(GREY)
        y = top - 23 * mm
        address = seller.address
        for text in (address.street, address.additional_street, f'{address.postcode} {address.city}',
                     seller.contact.phone and f'Tel. {seller.contact.phone}',
                     seller.contact.email or seller.email):
            if text and text.strip():
                canvas.drawRightString(RIGHT, y, text.strip())
                y -= 10.5
        canvas.setFont(regular, 7)
        sender = ' · '.join(part for part in (seller.name, address.street,
                                               f'{address.postcode} {address.city}'.strip()) if part)
        canvas.drawString(LEFT, top - 50 * mm, sender[:95])
        canvas.setStrokeColor(LINE)
        canvas.line(LEFT, top - 51 * mm, LEFT + 85 * mm, top - 51 * mm)
        canvas.setFillColor(colors.black)
        canvas.setFont(regular, 10)
        y = top - 57 * mm
        b = buyer.address
        recipient = [buyer.name, buyer.contact.name and f'z. Hd. {buyer.contact.name}', b.street,
                     b.additional_street, f'{b.postcode} {b.city}'.strip(), b.country if b.country != 'DE' else '']
        for text in recipient:
            if text:
                canvas.drawString(LEFT, y, text[:60])
                y -= 12.5
        y = top - 50 * mm
        for label, value in info:
            canvas.setFont(regular, 8.5)
            canvas.setFillColor(GREY)
            canvas.drawString(125 * mm, y, label)
            canvas.setFillColor(colors.black)
            canvas.drawRightString(RIGHT, y, value[:28])
            y -= 11.5
        canvas.restoreState()
        footer(canvas, doc)

    def later_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(GREY)
        canvas.drawString(LEFT, A4[1] - 14 * mm, f'{title} · {seller.name}')
        canvas.restoreState()
        footer(canvas, doc)

    def footer(canvas, doc):
        canvas._er_font = regular
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(LEFT, 27 * mm, RIGHT, 27 * mm)
        address = seller.address
        columns = [
            [seller.name, address.street, f'{address.postcode} {address.city}'.strip()],
            [seller.vat_id and f'USt-IdNr.: {seller.vat_id}', seller.tax_number and f'Steuernr.: {seller.tax_number}',
             seller.register_id, seller.legal_info],
            [payment.iban and (payment.account_name or seller.name), payment.iban and f'IBAN: {iban_groups(payment.iban)}',
             payment.bic and f'BIC: {payment.bic}'],
        ]
        foot = ParagraphStyle('foot', fontName=regular, fontSize=7, leading=8.6, textColor=GREY)
        width = WIDTH / 3
        for index, column in enumerate(columns):
            text = '<br/>'.join(escape(line) for line in column if line)
            para = Paragraph(text, foot)
            _, height = para.wrap(width - 4 * mm, 20 * mm)
            para.drawOn(canvas, LEFT + index * width, 25 * mm - height)
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4, leftMargin=LEFT, rightMargin=20 * mm, topMargin=20 * mm,
                          bottomMargin=30 * mm, title=title, author=seller.name,
                          subject=f'{title} von {seller.name}', creator=PRODUCER, producer=PRODUCER,
                          invariant=1)
    first_frame = Frame(LEFT, 30 * mm, WIDTH, A4[1] - 30 * mm - header_height, id='first', leftPadding=0,
                        rightPadding=0, topPadding=0, bottomPadding=0)
    later_frame = Frame(LEFT, 30 * mm, WIDTH, A4[1] - 30 * mm - 20 * mm, id='later', leftPadding=0,
                        rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate('first', [first_frame], onPage=first_page),
                          PageTemplate('later', [later_frame], onPage=later_page)])

    story = [NextPageTemplate('later'), p(escape(title), heading)]
    if invoice.preceding_number:
        story.append(p(escape(f'zu Rechnung {invoice.preceding_number}'
                              + (f' vom {de_date(invoice.preceding_date)}' if invoice.preceding_date else ''))))
    for note in invoice.notes:
        story.append(p(escape(note).replace('\n', '<br/>')))
    story.append(Spacer(0, 5 * mm))

    header = ['Pos.', 'Bezeichnung', 'Menge', 'Einheit', 'Einzelpreis', 'USt', 'Betrag']
    rows = [[p(f'<b>{text}</b>', cell_right if index in (2, 4, 5, 6) else cell)
             for index, text in enumerate(header)]]
    for line in invoice.lines:
        parts = [f'<font name="{bold}">{escape(line.name)}</font>']
        if line.description:
            parts.append(escape(line.description).replace('\n', '<br/>'))
        details = [f'Art.-Nr. {line.item_id}' if line.item_id else '', line.note]
        if any(details):
            parts.append(f'<font color="#555555">{escape(" · ".join(d for d in details if d))}</font>')
        if line.discount_amount:
            percent = f' {de_quantity(line.discount_percent)} %' if line.discount_percent is not None else ''
            parts.append(f'<font color="#555555">abzgl.{percent} {escape(line.discount_reason)}: '
                         f'−{de_amount(line.discount_amount, currency)}</font>')
        if line.charge_amount:
            parts.append(f'<font color="#555555">zzgl. {escape(line.charge_reason)}: '
                         f'{de_amount(line.charge_amount, currency)}</font>')
        rows.append([p(escape(line.id), cell), p('<br/>'.join(parts), cell),
                     p(de_quantity(line.quantity), cell_right), p(escape(codes.UNITS.get(line.unit, line.unit)), cell),
                     p(de_price(line.price, currency)
                       + (f' / {de_quantity(line.base_quantity)}' if line.base_quantity != 1 else ''), cell_right),
                     p(de_rate(line.vat_rate), cell_right), p(de_amount(line.net, currency), cell_right)])
    table = Table(rows, colWidths=[10 * mm, 72 * mm, 18 * mm, 16 * mm, 22 * mm, 12 * mm, 20 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), regular),
        ('BACKGROUND', (0, 0), (-1, 0), SHADE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, LINE),
        ('LINEBELOW', (0, 1), (-1, -1), 0.3, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(0, 4 * mm)]

    total_rows = [('Summe Positionen (netto)', invoice.line_total, False)]
    total_rows += [(f'abzgl. {item.reason}', -item.amount, False) for item in invoice.allowances]
    total_rows += [(f'zzgl. {item.reason}', item.amount, False) for item in invoice.charges]
    if invoice.allowances or invoice.charges:
        total_rows.append(('Nettobetrag', invoice.tax_basis_total, False))
    for vat in invoice.vat_groups:
        if vat.category == 'S':
            label = f'USt {de_rate(vat.rate)} auf {de_amount(vat.basis, currency)}'
        else:
            label = f'USt 0 % ({vat.category}) auf {de_amount(vat.basis, currency)}'
        total_rows.append((label, vat.amount, False))
    total_rows.append(('Gesamtbetrag', invoice.grand_total, True))
    if invoice.prepaid:
        total_rows.append(('abzgl. bereits gezahlt', -invoice.prepaid, False))
    if invoice.rounding:
        total_rows.append(('Rundung', invoice.rounding, False))
    if invoice.prepaid or invoice.rounding:
        total_rows.append(('Zahlbetrag', invoice.due_amount, True))
    totals = Table([[p(f'<b>{escape(label)}</b>' if strong else escape(label), cell),
                     p(f'<b>{de_amount(value, currency)}</b>' if strong else de_amount(value, currency), cell_right)]
                    for label, value, strong in total_rows],
                   colWidths=[62 * mm, 28 * mm], hAlign='RIGHT')
    strong_rows = [index for index, row in enumerate(total_rows) if row[2]]
    totals.setStyle(TableStyle([('FONTNAME', (0, 0), (-1, -1), regular), ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                                ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]
                               + [('LINEABOVE', (0, i), (-1, i), 0.6, colors.black) for i in strong_rows]
                               + [('BACKGROUND', (0, i), (-1, i), SHADE) for i in strong_rows]))
    story += [totals, Spacer(0, 5 * mm)]

    for vat in invoice.vat_groups:
        if vat.exemption_reason:
            story.append(p(escape(f'Hinweis zur Umsatzsteuer: {vat.exemption_reason}')))
    story.append(Spacer(0, 2 * mm))

    due = de_amount(invoice.due_amount, currency)
    if invoice.due_amount <= 0 or invoice.is_credit_note:
        story.append(p(escape(f'Der Betrag von {de_amount(abs(invoice.due_amount), currency)} '
                              'wird Ihnen gutgeschrieben.')))
    elif payment.means in codes.CREDIT_TRANSFER:
        until = f' bis zum {de_date(invoice.due_date)}' if invoice.due_date else ''
        story.append(p(escape(f'Bitte überweisen Sie {due}{until} unter Angabe von „{payment.reference}“ '
                              'auf folgendes Konto:')
                       + f'<br/>{escape(payment.account_name or seller.name)} · IBAN {iban_groups(payment.iban)}'
                       + (f' · BIC {escape(payment.bic)}' if payment.bic else '')))
    elif payment.means in codes.DIRECT_DEBIT:
        story.append(p(escape(f'Der Betrag von {due} wird per SEPA-Lastschrift von Ihrem Konto '
                              f'IBAN {iban_groups(payment.debtor_iban)} eingezogen (Mandatsreferenz '
                              f'{payment.mandate_id}, Gläubiger-ID {payment.creditor_id})'
                              + (f' frühestens am {de_date(invoice.due_date)}.' if invoice.due_date else '.'))))
    else:
        story.append(p(escape(f'Zahlungsart: {codes.PAYMENT_MEANS.get(payment.means, payment.means)}.')))
    if payment.terms or (payment.discount_days and payment.discount_percent):
        terms = [text for text in invoice.payment_terms_text.split('\n') if text and not text.startswith('#')]
        story.append(p('<br/>'.join(escape(text) for text in terms)))
    if invoice.delivery_address:
        a = invoice.delivery_address
        story.append(p(escape('Lieferanschrift: ' + ', '.join(
            part for part in (invoice.delivery_name, a.street, f'{a.postcode} {a.city}'.strip(), a.country) if part))))
    if embedded_note:
        story += [Spacer(0, 6 * mm), p(escape(embedded_note), small)]

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()


# ---------------------------------------------------------------- ZUGFeRD / Factur-X (PDF/A-3b)

FX_NAMESPACE = 'urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#'


def _pdf_date(moment):
    return moment.strftime("D:%Y%m%d%H%M%S+00'00'")


def _xmp(title, author, subject, moment, filename, conformance):
    stamp = moment.strftime('%Y-%m-%dT%H:%M:%S+00:00')
    title, author, subject = escape(title), escape(author), escape(subject)
    fields = [('DocumentFileName', 'The name of the embedded XML document'),
              ('DocumentType', 'The type of the hybrid document in capital letters, e.g. INVOICE or ORDER'),
              ('Version', 'The actual version of the standard applying to the embedded XML document'),
              ('ConformanceLevel', 'The conformance level of the embedded XML document')]
    properties = ''.join(
        f'<rdf:li rdf:parseType="Resource"><pdfaProperty:name>{name}</pdfaProperty:name>'
        f'<pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external'
        f'</pdfaProperty:category><pdfaProperty:description>{text}</pdfaProperty:description></rdf:li>'
        for name, text in fields)
    return f'''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
<pdfaid:part>3</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>
</rdf:Description>
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
<dc:creator><rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq></dc:creator>
<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{subject}</rdf:li></rdf:Alt></dc:description>
</rdf:Description>
<rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
<pdf:Producer>{PRODUCER}</pdf:Producer>
</rdf:Description>
<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
<xmp:CreatorTool>{PRODUCER}</xmp:CreatorTool><xmp:CreateDate>{stamp}</xmp:CreateDate>
<xmp:ModifyDate>{stamp}</xmp:ModifyDate><xmp:MetadataDate>{stamp}</xmp:MetadataDate>
</rdf:Description>
<rdf:Description rdf:about="" xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
 xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#" xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
<pdfaExtension:schemas><rdf:Bag><rdf:li rdf:parseType="Resource">
<pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
<pdfaSchema:namespaceURI>{FX_NAMESPACE}</pdfaSchema:namespaceURI>
<pdfaSchema:prefix>fx</pdfaSchema:prefix>
<pdfaSchema:property><rdf:Seq>{properties}</rdf:Seq></pdfaSchema:property>
</rdf:li></rdf:Bag></pdfaExtension:schemas>
</rdf:Description>
<rdf:Description rdf:about="" xmlns:fx="{FX_NAMESPACE}">
<fx:DocumentType>INVOICE</fx:DocumentType><fx:DocumentFileName>{filename}</fx:DocumentFileName>
<fx:Version>1.0</fx:Version><fx:ConformanceLevel>{conformance}</fx:ConformanceLevel>
</rdf:Description>
</rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''.encode('utf-8')


def _srgb_profile():
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def zugferd(invoice, xml, conformance='EN 16931', moment=None):
    """PDF/A-3b with the CII XML attached as factur-x.xml (xrechnung.xml for the XRECHNUNG profile)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
                               TextStringObject)

    filename = 'xrechnung.xml' if conformance == 'XRECHNUNG' else 'factur-x.xml'
    profile = 'XRECHNUNG' if conformance == 'XRECHNUNG' else 'EN 16931'
    note = (f'Dies ist eine E-Rechnung nach ZUGFeRD 2.3 / Factur-X (Profil {profile}). Maßgeblich sind die '
            f'eingebetteten strukturierten Rechnungsdaten ({filename}).')
    moment = (moment or datetime.now(timezone.utc)).replace(microsecond=0)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(visual(invoice, note))))
    writer.pdf_header = b'%PDF-1.7'
    catalog = writer._root_object

    title = f'{codes.TYPE_TITLES.get(invoice.type_code, "Rechnung")} {invoice.number}'
    author = invoice.seller.name
    subject = f'{title} von {author}'
    info = writer._info.get_object()
    for key in list(info.keys()):
        del info[key]
    for key, value in (('/Title', title), ('/Author', author), ('/Subject', subject), ('/Creator', PRODUCER),
                       ('/Producer', PRODUCER), ('/CreationDate', _pdf_date(moment)),
                       ('/ModDate', _pdf_date(moment))):
        info[NameObject(key)] = TextStringObject(value)

    metadata = DecodedStreamObject()
    metadata.set_data(_xmp(title, author, subject, moment, filename, profile))
    metadata.update({NameObject('/Type'): NameObject('/Metadata'), NameObject('/Subtype'): NameObject('/XML')})
    catalog[NameObject('/Metadata')] = writer._add_object(metadata)

    icc = DecodedStreamObject()
    icc.set_data(_srgb_profile())
    icc[NameObject('/N')] = NumberObject(3)
    intent = DictionaryObject({
        NameObject('/Type'): NameObject('/OutputIntent'),
        NameObject('/S'): NameObject('/GTS_PDFA1'),
        NameObject('/OutputConditionIdentifier'): TextStringObject('sRGB IEC61966-2.1'),
        NameObject('/Info'): TextStringObject('sRGB IEC61966-2.1'),
        NameObject('/DestOutputProfile'): writer._add_object(icc),
    })
    catalog[NameObject('/OutputIntents')] = ArrayObject([writer._add_object(intent)])

    embedded = DecodedStreamObject()
    embedded.set_data(xml)
    embedded.update({
        NameObject('/Type'): NameObject('/EmbeddedFile'),
        NameObject('/Subtype'): NameObject('/text/xml'),
        NameObject('/Params'): DictionaryObject({NameObject('/ModDate'): TextStringObject(_pdf_date(moment)),
                                                 NameObject('/Size'): NumberObject(len(xml))}),
    })
    embedded_ref = writer._add_object(embedded)
    filespec = DictionaryObject({
        NameObject('/Type'): NameObject('/Filespec'),
        NameObject('/F'): TextStringObject(filename),
        NameObject('/UF'): TextStringObject(filename),
        NameObject('/Desc'): TextStringObject('Factur-X / ZUGFeRD Rechnungsdaten'),
        NameObject('/AFRelationship'): NameObject('/Alternative'),
        NameObject('/EF'): DictionaryObject({NameObject('/F'): embedded_ref, NameObject('/UF'): embedded_ref}),
    })
    filespec_ref = writer._add_object(filespec)
    catalog[NameObject('/Names')] = DictionaryObject({NameObject('/EmbeddedFiles'): DictionaryObject({
        NameObject('/Names'): ArrayObject([TextStringObject(filename), filespec_ref])})})
    catalog[NameObject('/AF')] = ArrayObject([filespec_ref])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def embedded_xml(pdf_bytes):
    """The invoice XML attached to a ZUGFeRD / Factur-X / XRechnung PDF, or None."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    attachments = reader.attachments
    for name in ('factur-x.xml', 'xrechnung.xml', 'zugferd-invoice.xml', 'ZUGFeRD-invoice.xml'):
        if name in attachments:
            return attachments[name][0]
    for name, contents in attachments.items():
        if name.lower().endswith('.xml'):
            return contents[0]
    return None
