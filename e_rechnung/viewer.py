"""Shows a received e-invoice as a readable HTML page and checks it (rules and arithmetic)."""
from decimal import Decimal, InvalidOperation
from html import escape

from . import checks, codes
from .fmt import de_amount, de_date, de_price, de_quantity, de_rate, iban_groups
from .model import Issue, money, parse_date, parse_invoice
from .reader import read


def inspect(content):
    """Read an XML or PDF e-invoice. Returns (data, issues): the input-format dict and all findings."""
    data = read(content)
    invoice, issues = parse_invoice(data)
    profile = 'xrechnung' if 'xrechnung' in data.get('specification', '') else 'en16931'
    issues += checks.check(invoice, profile, receiving=True)
    issues += arithmetic(data, invoice)
    return data, issues


def _dec(text):
    try:
        return Decimal(str(text)) if text not in (None, '') else None
    except InvalidOperation:
        return None


def arithmetic(data, invoice):
    """Compare the totals the document states with a fresh EN 16931 calculation (BR-CO-10 ff.)."""
    issues = []
    stated = data.get('stated', {})
    for index, (raw, line) in enumerate(zip(data.get('lines', []), invoice.lines)):
        net = _dec(raw.get('net'))
        if net is not None and abs(net - line.net) > Decimal('0.01'):
            issues.append(Issue('fehler', f'lines[{index}]', f'Positionsbetrag {net} stimmt nicht: Menge × Preis '
                                f'− Rabatt ergibt {line.net}.'))
    for key, label, computed, rule in (
            ('line_total', 'Summe der Positionen', invoice.line_total, 'BR-CO-10'),
            ('tax_basis_total', 'Nettobetrag', invoice.tax_basis_total, 'BR-CO-13'),
            ('tax_total', 'Umsatzsteuer gesamt', invoice.tax_total, 'BR-CO-14'),
            ('grand_total', 'Bruttobetrag', invoice.grand_total, 'BR-CO-15'),
            ('due_amount', 'Zahlbetrag', invoice.due_amount, 'BR-CO-16')):
        value = _dec(stated.get(key))
        if value is not None and value != computed:
            issues.append(Issue('fehler', f'stated.{key}', f'{label} laut Rechnung {value}, nachgerechnet {computed}.',
                                rule))
    computed_groups = {(group.category, money(group.rate) if group.rate is not None else None): group
                       for group in invoice.vat_groups}
    for group in stated.get('vat_groups', []):
        rate = _dec(group.get('rate'))
        key = (group.get('category'), money(rate) if rate is not None and group.get('category') != 'O' else None)
        mine = computed_groups.get(key)
        amount, basis = _dec(group.get('amount')), _dec(group.get('basis'))
        label = f'USt-Gruppe {group.get("category")} {group.get("rate", "")} %'
        if mine is None:
            issues.append(Issue('fehler', 'stated.vat_groups', f'{label} passt zu keiner Position.', 'BR-CO-18'))
            continue
        if basis is not None and basis != mine.basis:
            issues.append(Issue('fehler', 'stated.vat_groups', f'{label}: Bemessungsgrundlage {basis}, '
                                f'nachgerechnet {mine.basis}.', 'BR-CO-18'))
        if amount is not None and abs(amount - mine.amount) > Decimal('0.01'):
            issues.append(Issue('fehler', 'stated.vat_groups', f'{label}: Steuerbetrag {amount}, '
                                f'nachgerechnet {mine.amount}.', 'BR-CO-17'))
    return issues


# ---------------------------------------------------------------- HTML

STYLE = '''
:root{--ink:#1d2430;--muted:#5b6575;--line:#d9dee6;--soft:#f3f5f8;--accent:#1f5fae;--bad:#b42318;--warn:#8a5a00;--ok:#1e7a3c;--bg:#fff}
@media (prefers-color-scheme:dark){:root{--ink:#e6e9ee;--muted:#a3acb9;--line:#3a4250;--soft:#222833;--accent:#7fb0ff;--bad:#ff8a80;--warn:#f5c26b;--ok:#7ad69a;--bg:#161a21}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px 48px}h1{font-size:24px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.meta{color:var(--muted);font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:8px;padding:12px 14px}.card b{display:block;margin-bottom:2px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;margin:0}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600}td.n,th.n{text-align:right;white-space:nowrap}.sub{color:var(--muted);font-size:13px}
.totals{max-width:420px;margin-left:auto}.totals td:first-child{color:var(--muted)}.totals tr.strong td{font-weight:700;color:var(--ink);border-top:2px solid var(--ink)}
.issues{list-style:none;padding:0;margin:0}.issues li{padding:6px 10px;border-left:4px solid;margin:4px 0;background:var(--soft);border-radius:4px}
.issues .fehler{border-color:var(--bad)}.issues .hinweis{border-color:var(--warn)}.ok{color:var(--ok);font-weight:600}.badge{font-size:12px;color:var(--muted)}
'''


def _e(value):
    return escape(str(value if value is not None else ''))


def _amount(text, currency):
    value = _dec(text)
    return de_amount(value, currency) if value is not None else ''


def _d(text):
    try:
        return de_date(parse_date(text)) if text else ''
    except ValueError:
        return _e(text)


def _party(title, party):
    if not party:
        return ''
    contact = party.get('contact', {})
    rows = [('Anschrift', ', '.join(filter(None, (party.get('street'), party.get('additional_street'),
                                                 f"{party.get('postcode', '')} {party.get('city', '')}".strip(),
                                                 party.get('country'))))),
            ('Handelsname', party.get('trading_name')), ('Kennung', party.get('id')),
            ('Register', party.get('register_id')), ('USt-IdNr.', party.get('vat_id')),
            ('Steuernummer', party.get('tax_number')), ('Rechtliches', party.get('legal_info')),
            ('E-Rechnung an', party.get('email')), ('Kontakt', contact.get('name')),
            ('Telefon', contact.get('phone')), ('E-Mail', contact.get('email'))]
    body = ''.join(f'<dt>{_e(k)}</dt><dd>{_e(v)}</dd>' for k, v in rows if v)
    return f'<div class="card"><b>{_e(title)}</b><div>{_e(party.get("name"))}</div><dl>{body}</dl></div>'


def html(data, issues):
    """Standalone HTML page for a read e-invoice (works offline, light and dark mode)."""
    currency = data.get('currency', 'EUR')
    stated = data.get('stated', {})
    type_code = data.get('type', '')
    title = f'{codes.TYPE_TITLES.get(type_code, "Rechnung")} {data.get("number", "")}'
    spec = data.get('specification', '')
    format_label = ('XRechnung' if 'xrechnung' in spec else 'EN 16931 / ZUGFeRD') + f' · {data.get("syntax", "")}'
    if data.get('container'):
        format_label += f' · {data["container"]}'

    period = data.get('period', {})
    details = [('Rechnungsart', f'{type_code} – {codes.TYPES.get(type_code, "unbekannt")}'),
               ('Rechnungsdatum', _d(data.get('issue_date'))), ('Fällig am', _d(data.get('due_date'))),
               ('Leistungsdatum', _d(data.get('delivery_date'))),
               ('Leistungszeitraum', ' – '.join(filter(None, (_d(period.get('start')), _d(period.get('end')))))),
               ('Käuferreferenz / Leitweg-ID', data.get('buyer_reference')),
               ('Bestellnummer', data.get('order_reference')), ('Vertrag', data.get('contract_reference')),
               ('Projekt', data.get('project_reference')),
               ('Bezug auf Rechnung', ' vom '.join(filter(None, (data.get('preceding_invoice', {}).get('number'),
                                                                 _d(data.get('preceding_invoice', {}).get('date')))))),
               ('Währung', currency)]
    details_html = ''.join(f'<dt>{_e(k)}</dt><dd>{_e(v)}</dd>' for k, v in details if v)

    rows = []
    for line in data.get('lines', []):
        sub = [_e(line.get('description'))] if line.get('description') else []
        extra = ' · '.join(filter(None, (line.get('item_id') and f'Art.-Nr. {line["item_id"]}', line.get('note'))))
        if extra:
            sub.append(_e(extra))
        discount = line.get('discount', {})
        if discount.get('amount'):
            sub.append(_e(f'abzgl. {discount.get("reason", "Rabatt")}: {_amount(discount["amount"], currency)}'))
        surcharge = line.get('surcharge', {})
        if surcharge.get('amount'):
            sub.append(_e(f'zzgl. {surcharge.get("reason", "Zuschlag")}: {_amount(surcharge["amount"], currency)}'))
        unit = line.get('unit', '')
        price = _dec(line.get('price'))
        rate = _dec(line.get('vat_rate'))
        rows.append(
            f'<tr><td>{_e(line.get("id"))}</td><td>{_e(line.get("name"))}'
            + ''.join(f'<div class="sub">{s}</div>' for s in sub)
            + f'</td><td class="n">{_e(de_quantity(_dec(line.get("quantity")) or Decimal(0)))}</td>'
            f'<td>{_e(codes.UNITS.get(unit, unit))}</td>'
            f'<td class="n">{_e(de_price(price, currency) if price is not None else "")}</td>'
            f'<td class="n">{_e(line.get("vat_category", ""))} {_e(de_rate(rate) if rate is not None else "")}</td>'
            f'<td class="n">{_e(_amount(line.get("net"), currency))}</td></tr>')

    total_rows = [('Summe Positionen', stated.get('line_total'), False)]
    total_rows += [(f'abzgl. {a.get("reason", "Nachlass")}', '-' + a.get('amount', '0'), False)
                   for a in data.get('allowances', [])]
    total_rows += [(f'zzgl. {c.get("reason", "Zuschlag")}', c.get('amount'), False) for c in data.get('charges', [])]
    total_rows.append(('Nettobetrag', stated.get('tax_basis_total'), False))
    for group in stated.get('vat_groups', []):
        rate = _dec(group.get('rate'))
        total_rows.append((f'USt {group.get("category")} {de_rate(rate) if rate is not None else ""} auf '
                           f'{_amount(group.get("basis"), currency)}', group.get('amount'), False))
    total_rows.append(('Bruttobetrag', stated.get('grand_total'), True))
    if stated.get('prepaid'):
        total_rows.append(('abzgl. bereits gezahlt', '-' + stated['prepaid'], False))
    if stated.get('rounding'):
        total_rows.append(('Rundung', stated['rounding'], False))
    total_rows.append(('Zahlbetrag', stated.get('due_amount'), True))
    totals_html = ''.join(f'<tr class="{"strong" if strong else ""}"><td>{_e(label)}</td>'
                          f'<td class="n">{_e(_amount(value, currency))}</td></tr>'
                          for label, value, strong in total_rows if value not in (None, ''))

    payment = data.get('payment', {})
    discount = payment.get('discount', {})
    pay = [('Zahlungsart', f'{payment.get("means", "")} – '
                           f'{payment.get("info") or codes.PAYMENT_MEANS.get(payment.get("means", ""), "")}'),
           ('Kontoinhaber', payment.get('account_name')),
           ('IBAN', iban_groups(payment.get('iban', '')) if payment.get('iban') else ''),
           ('BIC', payment.get('bic')), ('Verwendungszweck', payment.get('reference')),
           ('Zahlungsbedingungen', payment.get('terms')),
           ('Skonto', f'{de_quantity(_dec(discount.get("percent")) or Decimal(0))} % innerhalb {discount.get("days")} Tagen'
            if discount else ''),
           ('Mandatsreferenz', payment.get('mandate_id')), ('Gläubiger-ID', payment.get('creditor_id')),
           ('Belastetes Konto', payment.get('debtor_iban'))]
    pay_html = ''.join(f'<dt>{_e(k)}</dt><dd>{_e(v)}</dd>' for k, v in pay if v)
    reasons = [g.get('exemption_reason') for g in stated.get('vat_groups', []) if g.get('exemption_reason')]
    notes = data.get('note', [])

    errors = [i for i in issues if i.level == 'fehler']
    if issues:
        issue_html = '<ul class="issues">' + ''.join(
            f'<li class="{_e(i.level)}"><b>{_e(i.level.capitalize())}</b> {_e(i.message)} '
            f'<span class="badge">{_e(i.field)}{" · " + _e(i.rule) if i.rule else ""}</span></li>' for i in issues) + '</ul>'
    else:
        issue_html = '<p class="ok">Keine Auffälligkeiten gefunden.</p>'
    summary = (f'<span style="color:var(--bad)">{len(errors)} Fehler</span>' if errors
               else '<span class="ok">Inhaltlich in Ordnung</span>')
    delivery = data.get('delivery', {})
    delivery_html = ''
    if delivery:
        delivery_html = (f'<div class="card"><b>Lieferanschrift</b><div>{_e(delivery.get("name"))}</div><div>'
                         + _e(', '.join(filter(None, (delivery.get('street'),
                                                      f"{delivery.get('postcode', '')} {delivery.get('city', '')}".strip(),
                                                      delivery.get('country'))))) + '</div></div>')

    return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>{STYLE}</style></head><body><main class="wrap">
<h1>{_e(title)}</h1><div class="meta">{_e(format_label)} · {summary}</div>
<div class="meta">{_e(spec)}</div>
<h2>Parteien</h2><div class="grid">{_party("Verkäufer", data.get("seller"))}{_party("Käufer", data.get("buyer"))}{delivery_html}</div>
<h2>Angaben</h2><div class="card"><dl>{details_html}</dl></div>
{"<h2>Bemerkungen</h2><div class='card'>" + "<br>".join(_e(n) for n in notes) + "</div>" if notes else ""}
<h2>Positionen</h2><div class="scroll"><table><thead><tr><th>Pos.</th><th>Bezeichnung</th><th class="n">Menge</th>
<th>Einheit</th><th class="n">Einzelpreis</th><th class="n">USt</th><th class="n">Betrag</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h2>Summen</h2><table class="totals">{totals_html}</table>
{"".join(f'<p class="sub">Hinweis zur Umsatzsteuer: {_e(r)}</p>' for r in dict.fromkeys(reasons))}
<h2>Zahlung</h2><div class="card"><dl>{pay_html}</dl></div>
<h2>Prüfung</h2>{issue_html}
<p class="sub">Inhaltliche Prüfung nach EN 16931 / XRechnung (Pflichtangaben und Rechenwerte). Die verbindliche
technische Prüfung ist der KoSIT-Validator.</p>
</main></body></html>'''
