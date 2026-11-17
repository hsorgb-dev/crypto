"""ZUGFeRD/Factur-X: lesbare PDF-Rechnung mit eingebetteter CII-XML (PDF/A-3).

Optionale Abhängigkeiten: reportlab (Layout), pypdf, Pillow (sRGB-Profil)
und factur-x (Einbettung + XMP-Metadaten):  pip install reportlab factur-x
"""
from __future__ import annotations

import io
import os
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from .calc import Totals
from .model import TYPE_CODES, UNIT_LABELS, VAT_CATEGORIES, Invoice
from .xmlutil import payment_terms_text

FONT_CANDIDATES = [
    (os.environ.get("ERECHNUNG_FONT"), os.environ.get("ERECHNUNG_FONT_BOLD")),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
TITLES = {"380": "Rechnung", "381": "Gutschrift", "384": "Korrigierte Rechnung", "326": "Teilrechnung",
          "389": "Gutschrift (Selbstfakturierung)", "875": "Teilschlussrechnung", "876": "Abschlagsrechnung",
          "877": "Schlussrechnung"}


def _fonts() -> tuple[str, str]:
    """Registriert eine TrueType-Schrift (PDF/A verlangt eingebettete Schriften)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in FONT_CANDIDATES:
        if regular and bold and Path(regular).exists() and Path(bold).exists():
            if "ER-Regular" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ER-Regular", regular))
                pdfmetrics.registerFont(TTFont("ER-Bold", bold))
            return "ER-Regular", "ER-Bold"
    # Rückfall: Standardschrift (nicht eingebettet, dann kein striktes PDF/A)
    return "Helvetica", "Helvetica-Bold"


def de_number(value: Decimal, decimals: int | None = None) -> str:
    if decimals is not None:
        value = value.quantize(Decimal(1).scaleb(-decimals))
        text = f"{value:,.{decimals}f}"
    else:
        text = f"{value.normalize():,f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def de_money(value: Decimal, currency: str) -> str:
    return f"{de_number(value, 2)} {'€' if currency == 'EUR' else currency}"


def _unit_price(value: Decimal, currency: str) -> str:
    decimals = max(2, -value.normalize().as_tuple().exponent)
    return f"{de_number(value, min(decimals, 6))} {'€' if currency == 'EUR' else currency}"


def render_pdf(invoice: Invoice, totals: Totals) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    from reportlab.platypus import Table as _Table

    regular, bold = _fonts()
    cur = invoice.currency
    s, b = invoice.seller, invoice.buyer
    base = ParagraphStyle("base", fontName=regular, fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=base, fontSize=7, leading=9, textColor=colors.HexColor("#555555"))
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=base, fontName=bold, fontSize=16, leading=20)
    title = ParagraphStyle("title", parent=base, fontName=bold, fontSize=13, leading=17)
    th = ParagraphStyle("th", parent=base, fontName=bold)
    thr = ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)

    def Table(rows, **kwargs):  # noqa: N802 - ersetzt reportlab.Table mit Schrift-Vorgabe
        table = _Table(rows, **kwargs)
        table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), regular)]))
        return table

    def p(text, style=base):
        return Paragraph(escape(text).replace("\n", "<br/>"), style)

    def addr_lines(party):
        a = party.address
        lines = [party.name]
        lines += [x for x in (a.street, a.additional) if x]
        lines.append(f"{a.postal_code or ''} {a.city or ''}".strip())
        if a.country_code != "DE":
            lines.append(a.country_code)
        return lines

    story = [p(s.trading_name or s.name, head),
             p(" · ".join(x for x in [s.name, s.address.street, f"{s.address.postal_code or ''} {s.address.city or ''}".strip()] if x), small),
             Spacer(1, 6 * mm)]

    meta = [("Rechnungsnummer", invoice.number), ("Rechnungsdatum", f"{invoice.issue_date:%d.%m.%Y}")]
    if invoice.delivery_date:
        meta.append(("Leistungsdatum", f"{invoice.delivery_date:%d.%m.%Y}"))
    if invoice.period_start or invoice.period_end:
        start = f"{invoice.period_start:%d.%m.%Y}" if invoice.period_start else ""
        end = f"{invoice.period_end:%d.%m.%Y}" if invoice.period_end else ""
        meta.append(("Leistungszeitraum", f"{start} – {end}"))
    if invoice.buyer_reference:
        meta.append(("Ihre Referenz", invoice.buyer_reference))
    if invoice.order_reference:
        meta.append(("Bestellnummer", invoice.order_reference))
    if invoice.contract_reference:
        meta.append(("Vertrag", invoice.contract_reference))
    if invoice.project_reference:
        meta.append(("Projekt", invoice.project_reference))
    if b.vat_id:
        meta.append(("Ihre USt-IdNr.", b.vat_id))
    if invoice.due_date:
        meta.append(("Fällig am", f"{invoice.due_date:%d.%m.%Y}"))
    meta_table = Table([[p(k, small), p(v)] for k, v in meta], colWidths=[29 * mm, 51 * mm])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    left = [p("\n".join(addr_lines(b)))]
    if invoice.delivery_party:
        left += [Spacer(1, 4 * mm), p("Lieferanschrift", small), p("\n".join(addr_lines(invoice.delivery_party)))]
    story.append(Table([[left, meta_table]], colWidths=[90 * mm, 80 * mm],
                       style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(Spacer(1, 8 * mm))
    story.append(p(f"{TITLES.get(invoice.type_code, TYPE_CODES.get(invoice.type_code, 'Rechnung'))} {invoice.number}", title))
    if invoice.preceding_invoice_number:
        ref = f"Bezug: Rechnung {invoice.preceding_invoice_number}"
        if invoice.preceding_invoice_date:
            ref += f" vom {invoice.preceding_invoice_date:%d.%m.%Y}"
        story.append(p(ref))
    for note in invoice.notes:
        story.append(p(note.text))
    story.append(Spacer(1, 4 * mm))

    rows = [[p("Pos.", th), p("Bezeichnung", th), p("Menge", thr), p("Einzelpreis", thr), p("USt", thr),
             p("Netto", thr)]]
    for line in invoice.lines:
        desc = [f'<font name="{bold}">{escape(line.name)}</font>']
        if line.description:
            desc.append(escape(line.description))
        if line.seller_item_id:
            desc.append(f"Art.-Nr. {escape(line.seller_item_id)}")
        if line.period_start or line.period_end:
            ps = f"{line.period_start:%d.%m.%Y}" if line.period_start else ""
            pe = f"{line.period_end:%d.%m.%Y}" if line.period_end else ""
            desc.append(f"Zeitraum {ps} – {pe}")
        for ac in line.allowances + line.charges:
            label = ac.reason or ("Zuschlag" if ac.is_charge else "Abschlag")
            pct = f" {de_number(ac.percent)} %" if ac.percent is not None else ""
            desc.append(f"{'+' if ac.is_charge else '−'} {escape(label)}{pct}: {de_money(ac.amount, cur)}")
        if line.note:
            desc.append(escape(line.note))
        unit = UNIT_LABELS.get(line.unit_code, line.unit_code)
        price = _unit_price(line.unit_price, cur)
        if line.price_base_quantity != 1:
            price += f" / {de_number(line.price_base_quantity)} {unit}"
        rate = "–" if line.vat_category == "O" else f"{de_number(line.vat_rate)} %"
        rows.append([p(line.id), Paragraph("<br/>".join(desc), base),
                     p(f"{de_number(line.quantity)} {unit}", right), p(price, right), p(rate, right),
                     p(de_money(totals.line_net[line.id], cur), right)])
    table = Table(rows, colWidths=[12 * mm, 70 * mm, 22 * mm, 26 * mm, 14 * mm, 26 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), regular),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
    ]))
    story += [table, Spacer(1, 4 * mm)]

    sums = [("Summe Positionen", totals.line_total)]
    for ac in invoice.allowances:
        sums.append((f"− {ac.reason or 'Abschlag'}" + (f" ({de_number(ac.percent)} %)" if ac.percent is not None else ""),
                     -ac.amount))
    for ac in invoice.charges:
        sums.append((f"+ {ac.reason or 'Zuschlag'}", ac.amount))
    sums.append(("Nettobetrag", totals.tax_basis_total))
    for g in totals.vat_groups:
        if g.category in ("S", "L", "M"):
            label = f"USt {de_number(g.rate)} % auf {de_money(g.taxable, cur)}"
        else:
            label = f"USt 0 % ({VAT_CATEGORIES.get(g.category, g.category)}) auf {de_money(g.taxable, cur)}"
        sums.append((label, g.tax))
    sums.append(("Gesamtbetrag", totals.grand_total))
    if totals.prepaid:
        sums.append(("Bereits gezahlt", -totals.prepaid))
    if totals.rounding:
        sums.append(("Rundung", totals.rounding))
    if totals.prepaid or totals.rounding:
        sums.append(("Zahlbetrag", totals.due_payable))
    right_bold = ParagraphStyle("right_bold", parent=right, fontName=bold)
    emphasized = {"Gesamtbetrag", "Zahlbetrag"}
    sum_rows = [[p(k, right_bold if k in emphasized else right),
                 p(de_money(v, cur), right_bold if k in emphasized else right)] for k, v in sums]
    sum_table = Table(sum_rows, colWidths=[110 * mm, 30 * mm], hAlign="RIGHT",
                      style=[("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
                      + [("LINEABOVE", (0, i), (-1, i), 0.8, colors.black)
                         for i, (k, _) in enumerate(sums) if k in emphasized])
    story += [sum_table, Spacer(1, 5 * mm)]

    info = []
    for g in totals.vat_groups:
        if g.exemption_reason:
            info.append(g.exemption_reason)
    pay = invoice.payment
    terms = payment_terms_text(invoice.payment_terms, invoice.skonto, structured=False)
    if terms:
        info.append(terms)
    if pay:
        if pay.means_code == "59":
            info.append(f"Der Betrag von {de_money(totals.due_payable, cur)} wird per SEPA-Lastschrift von Ihrem Konto "
                        f"{pay.debited_iban or ''} eingezogen (Mandat {pay.mandate_id or ''}, "
                        f"Gläubiger-ID {pay.creditor_id or ''}).")
        elif pay.iban:
            bank = f"IBAN {pay.iban}" + (f", BIC {pay.bic}" if pay.bic else "") + (f" ({pay.bank_name})" if pay.bank_name else "")
            info.append(f"Bitte überweisen Sie {de_money(totals.due_payable, cur)} auf folgendes Konto: "
                        f"{pay.account_name or s.name}, {bank}. "
                        f"Verwendungszweck: {pay.remittance_info or invoice.number}")
        elif pay.card_number:
            info.append(f"Bezahlt per Karte (endet auf {pay.card_number}).")
    if info:
        story.append(KeepTogether([p(x) for x in info]))
    story += [Spacer(1, 4 * mm),
              p("Diese Rechnung enthält eine eingebettete elektronische Rechnung (ZUGFeRD/Factur-X, Profil EN 16931). "
                "Maßgeblich sind die eingebetteten XML-Daten.", small)]

    footer = [s.name, " ".join(x for x in [s.address.street, s.address.postal_code, s.address.city] if x)]
    if s.contact:
        footer.append(" · ".join(x for x in [s.contact.phone, s.contact.email] if x))
    ids = []
    if s.vat_id:
        ids.append(f"USt-IdNr. {s.vat_id}")
    if s.tax_number:
        ids.append(f"Steuernr. {s.tax_number}")
    if s.registration_id:
        ids.append(s.registration_id)
    if s.legal_info:
        ids.append(s.legal_info)
    footer_text = [" · ".join(x for x in footer if x), " · ".join(ids)]

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7)
        canvas.setFillColor(colors.HexColor("#555555"))
        y = 14 * mm
        for text in footer_text:
            canvas.drawCentredString(A4[0] / 2, y, text)
            y -= 9
        canvas.drawRightString(A4[0] - 20 * mm, y, f"Seite {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=28 * mm, title=f"{TITLES.get(invoice.type_code, 'Rechnung')} {invoice.number}",
                            author=s.name, subject=f"E-Rechnung {invoice.number}", creator="erechnung",
                            lang="de-DE",
                            # Startschrift = eingebettete Schrift, sonst landet ein nicht
                            # eingebettetes Helvetica in den Ressourcen (verletzt PDF/A).
                            initialFontName=regular, initialFontSize=9)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def add_output_intent(pdf: bytes) -> bytes:
    """Fügt ein sRGB-OutputIntent hinzu (für PDF/A erforderlich)."""
    from PIL import ImageCms
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject, StreamObject,
                               TextStringObject)

    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    writer = PdfWriter(clone_from=io.BytesIO(pdf))
    stream = StreamObject()
    stream.set_data(icc)
    stream[NameObject("/N")] = NumberObject(3)
    intent = DictionaryObject({
        NameObject("/Type"): NameObject("/OutputIntent"),
        NameObject("/S"): NameObject("/GTS_PDFA1"),
        NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB IEC61966-2.1"),
        NameObject("/Info"): TextStringObject("sRGB IEC61966-2.1"),
        NameObject("/DestOutputProfile"): writer._add_object(stream),
    })
    writer._root_object[NameObject("/OutputIntents")] = ArrayObject([writer._add_object(intent)])
    writer.pdf_header = b"%PDF-1.7"  # PDF/A-3 basiert auf PDF 1.7 (eingebettete Dateien /AF)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_zugferd_pdf(invoice: Invoice, totals: Totals, xml: bytes) -> bytes:
    try:
        from facturx import generate_from_binary
    except ImportError as exc:  # pragma: no cover - abhängig von der Installation
        raise RuntimeError("Für ZUGFeRD-PDFs bitte 'pip install reportlab factur-x' ausführen") from exc
    pdf = add_output_intent(render_pdf(invoice, totals))
    return generate_from_binary(
        pdf, xml, flavor="factur-x", level="en16931", check_xsd=True, afrelationship="alternative", lang="de-DE",
        pdf_metadata={
            "author": invoice.seller.name,
            "keywords": "Factur-X, ZUGFeRD, E-Rechnung",
            "title": f"{invoice.seller.name}: {TITLES.get(invoice.type_code, 'Rechnung')} {invoice.number}",
            "subject": f"{TITLES.get(invoice.type_code, 'Rechnung')} {invoice.number} vom {invoice.issue_date:%d.%m.%Y}",
        },
    )
