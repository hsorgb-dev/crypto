"""Tests for the e-invoice generator (e_rechnung/). They run offline.

The official validation (KoSIT schematron for EN 16931 / XRechnung 3.0, veraPDF for PDF/A-3) runs
only with E_RECHNUNG_OFFIZIELL=1, Java and the Mustang jar (python -m e_rechnung validator-laden):

    E_RECHNUNG_OFFIZIELL=1 python -m pytest -q tests/test_e_rechnung.py
"""
import copy
import io
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from e_rechnung import InvoiceError, codes, create, totals, validate  # noqa: E402
from e_rechnung.checks import iban_ok  # noqa: E402
from e_rechnung.model import parse_decimal  # noqa: E402
from e_rechnung.pdf import embedded_xml  # noqa: E402
from e_rechnung.reader import read  # noqa: E402
from e_rechnung.viewer import html, inspect  # noqa: E402

EXAMPLE = json.loads((ROOT / 'e_rechnung' / 'beispiele' / 'rechnung.json').read_text(encoding='utf-8'))
CASES = {path.stem: json.loads(path.read_text(encoding='utf-8'))
         for path in sorted((ROOT / 'tests' / 'e_rechnung_faelle').glob('*.json'))}
CASES['beispiel'] = EXAMPLE
FORMATS = ['xrechnung-cii', 'xrechnung-ubl', 'zugferd', 'zugferd-xrechnung']
RAM = '{urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100}'


def xml_of(result):
    return embedded_xml(result.content) if result.content[:5] == b'%PDF-' else result.content


def rules(issues):
    return {issue.rule for issue in issues if issue.level == 'fehler'}


# ---------------------------------------------------------------- calculation

def test_example_totals_follow_en16931_rounding():
    invoice = create(EXAMPLE, 'xrechnung-cii').invoice
    assert [line.net for line in invoice.lines] == [Decimal('960.00'), Decimal('674.73'), Decimal('79.90')]
    assert invoice.line_total == Decimal('1714.63')
    assert invoice.charge_total == Decimal('15.00')
    assert [(g.category, g.rate, g.basis, g.amount) for g in invoice.vat_groups] == [
        ('S', Decimal('19'), Decimal('1649.73'), Decimal('313.45')),
        ('S', Decimal('7'), Decimal('79.90'), Decimal('5.59'))]
    assert invoice.grand_total == invoice.due_amount == Decimal('2048.67')
    assert str(invoice.due_date) == '2026-11-22'   # 30 days payment terms


def test_prepaid_percent_allowance_and_base_quantity():
    data = copy.deepcopy(EXAMPLE)
    data['lines'] = [{'name': 'Schrauben', 'quantity': 250, 'unit': 'H87', 'price': '4,20', 'price_basis_quantity': 100}]
    data['charges'] = []
    data['allowances'] = [{'percent': 10, 'reason': 'Rabatt'}]
    data['prepaid'] = 5
    invoice = create(data, 'xrechnung-cii').invoice
    assert invoice.lines[0].net == Decimal('10.50')
    assert invoice.allowance_total == Decimal('1.05')
    assert invoice.tax_basis_total == Decimal('9.45')
    assert invoice.tax_total == Decimal('1.80')
    assert invoice.due_amount == Decimal('6.25')


def test_german_number_formats_are_accepted():
    assert parse_decimal('1.234,56') == Decimal('1234.56')
    assert parse_decimal('87,5') == Decimal('87.5')
    assert parse_decimal('12.5') == Decimal('12.5')
    assert parse_decimal(0.1) == Decimal('0.1')


def test_small_business_uses_category_e_with_reason():
    invoice = create(CASES['kleinunternehmer'], 'xrechnung-cii').invoice
    assert [(g.category, g.amount) for g in invoice.vat_groups] == [('E', Decimal('0.00'))]
    assert '§ 19 UStG' in invoice.vat_groups[0].exemption_reason


def test_skonto_line_follows_br_de_18():
    text = create(EXAMPLE, 'xrechnung-cii').invoice.payment_terms_text
    assert text.endswith('\n#SKONTO#TAGE=10#PROZENT=2.00#\n')


# ---------------------------------------------------------------- checks

def test_missing_xrechnung_data_is_reported_with_rules():
    data = copy.deepcopy(EXAMPLE)
    del data['buyer_reference']
    del data['seller']['contact']
    data['payment']['iban'] = 'DE02120300000000202052'
    with pytest.raises(InvoiceError) as caught:
        create(data, 'xrechnung-cii')
    found = rules(caught.value.issues)
    assert {'BR-DE-15', 'BR-DE-5', 'BR-DE-6', 'BR-DE-7', 'BR-DE-19'} <= found
    fields = {issue.field for issue in caught.value.issues}
    assert {'buyer_reference', 'seller.contact.phone', 'payment.iban'} <= fields


def test_zugferd_en16931_does_not_need_xrechnung_fields():
    data = copy.deepcopy(EXAMPLE)
    del data['buyer_reference']
    del data['seller']['contact']
    del data['buyer']['email']
    assert create(data, 'zugferd').content[:5] == b'%PDF-'


def test_vat_rules():
    data = copy.deepcopy(CASES['reverse_charge'])
    del data['buyer']['vat_id']
    assert 'BR-AE-02' in rules(validate(data, 'xrechnung-cii')[1])
    data = copy.deepcopy(EXAMPLE)
    data['seller'].pop('vat_id')
    data['seller'].pop('tax_number')
    data['seller'].pop('register_id')
    found = rules(validate(data, 'xrechnung-cii')[1])
    assert {'BR-CO-26', 'BR-DE-16'} <= found
    data = copy.deepcopy(EXAMPLE)
    data['lines'][0].update(vat_category='E', vat_rate=None)
    assert 'BR-E-10' in rules(validate(data, 'xrechnung-cii')[1])
    data['vat_exemption_reason'] = 'Steuerfrei nach § 4 Nr. 21 UStG'
    assert 'BR-E-10' not in rules(validate(data, 'xrechnung-cii')[1])
    data['lines'][0]['vat_rate'] = 19
    assert 'BR-E-05' in rules(validate(data, 'xrechnung-cii')[1])


def test_bad_input_types_do_not_crash():
    for data in (None, [], {'lines': 'x'}, {'lines': [{'price': 'abc'}]}, {'issue_date': '31.02.2026'}):
        with pytest.raises(InvoiceError):
            create(data, 'xrechnung-cii')
    with pytest.raises(InvoiceError):
        create(EXAMPLE, 'pdf-x')


def test_iban_checksum():
    assert iban_ok('DE02120300000000202051')
    assert not iban_ok('DE02120300000000202052')


# ---------------------------------------------------------------- output

@pytest.mark.parametrize('name', sorted(CASES))
@pytest.mark.parametrize('fmt', FORMATS)
def test_every_case_builds_and_reads_back_identically(name, fmt):
    data = CASES[name]
    result = create(data, fmt)
    xml = xml_of(result)
    root = ET.fromstring(xml)
    if fmt == 'zugferd':
        assert root.find(f'.//{RAM}GuidelineSpecifiedDocumentContextParameter/{RAM}ID').text == codes.EN16931_ID
    elif fmt != 'xrechnung-ubl':
        assert codes.XRECHNUNG_ID.encode() in xml
    again = create(read(result.content), fmt)
    assert xml_of(again) == xml


def test_credit_note_becomes_ubl_creditnote():
    xml = create(CASES['gutschrift_381'], 'xrechnung-ubl').content
    root = ET.fromstring(xml)
    assert root.tag == '{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote'
    assert b'<cbc:CreditNoteTypeCode>381</cbc:CreditNoteTypeCode>' in xml


def test_zugferd_pdf_structure():
    from pypdf import PdfReader
    content = create(EXAMPLE, 'zugferd').content
    reader = PdfReader(io.BytesIO(content))
    catalog = reader.trailer['/Root']
    assert list(reader.attachments) == ['factur-x.xml']
    assert catalog['/AF'][0].get_object()['/AFRelationship'] == '/Alternative'
    xmp = catalog['/Metadata'].get_object().get_data().decode()
    assert '<pdfaid:part>3</pdfaid:part>' in xmp and '<fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>' in xmp
    assert catalog['/OutputIntents'][0].get_object()['/S'] == '/GTS_PDFA1'
    for page in reader.pages:
        for font in page['/Resources']['/Font'].values():
            descriptor = font.get_object().get('/FontDescriptor')
            assert descriptor is not None and '/FontFile2' in descriptor.get_object()
    assert 'Rechnung RE-2026-0042' in reader.pages[0].extract_text()
    xr = PdfReader(io.BytesIO(create(EXAMPLE, 'zugferd-xrechnung').content))
    assert list(xr.attachments) == ['xrechnung.xml']


def test_long_invoice_has_several_pages():
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(create(CASES['viele_positionen_mit_anzahlung'], 'zugferd').content))
    assert len(reader.pages) > 2
    assert f'Seite {len(reader.pages)} von {len(reader.pages)}' in reader.pages[-1].extract_text()


# ---------------------------------------------------------------- reading and viewing

def test_viewer_finds_manipulated_totals():
    xml = create(EXAMPLE, 'xrechnung-cii').content.replace(
        b'<ram:GrandTotalAmount>2048.67<', b'<ram:GrandTotalAmount>2148.67<')
    data, issues = inspect(xml)
    assert 'BR-CO-15' in rules(issues)
    page = html(data, issues)
    assert 'Bruttobetrag laut Rechnung 2148.67' in page and '2.148,67 €' in page


def test_viewer_on_clean_invoice():
    data, issues = inspect(create(EXAMPLE, 'zugferd').content)
    assert not [issue for issue in issues if issue.level == 'fehler']
    assert data['container'].startswith('PDF')
    assert 'Muster Software GmbH' in html(data, issues)


def test_foreign_invoice_features_are_hints_not_errors():
    xml = create(EXAMPLE, 'xrechnung-cii').content.replace(b'<ram:TypeCode>58</ram:TypeCode>',
                                                            b'<ram:TypeCode>48</ram:TypeCode>')
    _, issues = inspect(xml)
    assert not [issue for issue in issues if issue.level == 'fehler']
    assert any(issue.field == 'payment.means' and issue.level == 'hinweis' for issue in issues)


def test_line_surcharge():
    invoice = create(CASES['positionszuschlag'], 'xrechnung-cii').invoice
    assert invoice.lines[0].net == Decimal('1005.00')
    assert invoice.lines[1].net == Decimal('729.70')


def test_reader_rejects_doctype_and_unknown_files():
    for content in (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', b'<Foo/>', b'kein xml',
                    b'%PDF-1.4 kaputt'):
        with pytest.raises(Exception):
            read(content)


# ---------------------------------------------------------------- interfaces

@pytest.fixture()
def api():
    from e_rechnung.server import Handler
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


def post(url, body, content_type='application/json'):
    request = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': content_type})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def test_http_api(api):
    body = json.dumps(EXAMPLE).encode()
    status, headers, content = post(f'{api}/api/erstellen?format=zugferd', body)
    assert status == 200 and headers['Content-Type'] == 'application/pdf' and content[:5] == b'%PDF-'
    assert 'RE-2026-0042.pdf' in headers['Content-Disposition']
    status, _, content = post(f'{api}/api/pruefen?format=xrechnung-cii', body)
    assert status == 200 and json.loads(content)['totals']['due_amount'] == '2048.67'
    status, _, content = post(f'{api}/api/erstellen', json.dumps({'number': 'X'}).encode())
    assert status == 422 and any(i['rule'] == 'BR-DE-15' for i in json.loads(content)['issues'])
    xml = create(EXAMPLE, 'xrechnung-ubl').content
    status, _, content = post(f'{api}/api/umwandeln?format=xrechnung-cii', xml, 'application/xml')
    assert status == 200 and content == create(EXAMPLE, 'xrechnung-cii').content
    status, _, content = post(f'{api}/api/anzeigen', xml, 'application/xml')
    assert status == 200 and b'Rechnung RE-2026-0042' in content
    status, _, content = post(f'{api}/api/lesen', b'<Foo/>', 'application/xml')
    assert status == 422
    with urllib.request.urlopen(f'{api}/') as response:
        assert b'E-Rechnung' in response.read()
    with urllib.request.urlopen(f'{api}/api/tools') as response:
        assert [tool['name'] for tool in json.load(response)] == [
            'e_rechnung_erstellen', 'e_rechnung_pruefen', 'e_rechnung_lesen']


def test_mcp_server_over_stdio(tmp_path):
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
         'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
         'params': {'name': 'e_rechnung_erstellen', 'arguments': {'invoice': EXAMPLE, 'format': 'zugferd'}}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
         'params': {'name': 'e_rechnung_erstellen', 'arguments': {'invoice': {'number': 'X'}}}},
        {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
         'params': {'name': 'e_rechnung_lesen', 'arguments': {'path': str(tmp_path / 'out' / 'RE-2026-0042.pdf')}}},
        {'jsonrpc': '2.0', 'id': 6, 'method': 'unbekannt'},
    ]
    run = subprocess.run([sys.executable, '-m', 'e_rechnung', 'mcp'], cwd=ROOT, capture_output=True, text=True,
                         input=''.join(json.dumps(m) + '\n' for m in messages), timeout=120,
                         env={**os.environ, 'E_RECHNUNG_AUSGABE': str(tmp_path / 'out')})
    replies = {reply['id']: reply for reply in map(json.loads, run.stdout.splitlines())}
    assert sorted(replies) == [1, 2, 3, 4, 5, 6]
    assert replies[1]['result']['capabilities'] == {'tools': {}}
    assert len(replies[2]['result']['tools']) == 3
    created = replies[3]['result']
    assert not created['isError'] and Path(created['structuredContent']['file']).is_file()
    assert created['structuredContent']['totals']['due_amount'] == '2048.67'
    assert replies[4]['result']['isError']
    assert replies[5]['result']['structuredContent']['data']['number'] == 'RE-2026-0042'
    assert replies[6]['error']['code'] == -32601


def test_cli(tmp_path):
    source = ROOT / 'e_rechnung' / 'beispiele' / 'rechnung.json'
    run = subprocess.run([sys.executable, '-m', 'e_rechnung', 'erstellen', str(source), '-f', 'xrechnung-ubl',
                          '-o', str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    target = tmp_path / 'RE-2026-0042.xml'
    assert target.read_bytes() == create(EXAMPLE, 'xrechnung-ubl').content
    run = subprocess.run([sys.executable, '-m', 'e_rechnung', 'pruefen', str(target)], cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 0 and 'keine Fehler' in run.stdout
    broken = tmp_path / 'kaputt.json'
    broken.write_text(json.dumps({'number': 'X'}))
    run = subprocess.run([sys.executable, '-m', 'e_rechnung', 'erstellen', str(broken)], cwd=tmp_path,
                         capture_output=True, text=True, timeout=120, env={**os.environ, 'PYTHONPATH': str(ROOT)})
    assert run.returncode == 2 and 'BR-DE-15' in run.stderr


def test_schema_file_matches_code():
    from e_rechnung.schema import SCHEMA
    assert json.loads((ROOT / 'e_rechnung' / 'schema.json').read_text(encoding='utf-8')) == SCHEMA


def test_totals_are_strings():
    invoice, _ = validate(EXAMPLE)
    assert totals(invoice)['vat'][0] == {'category': 'S', 'rate': '19', 'basis': '1649.73', 'amount': '313.45'}


# ---------------------------------------------------------------- official validation (opt-in)

def _official_ready():
    from e_rechnung import official
    return os.environ.get('E_RECHNUNG_OFFIZIELL') == '1' and official.available()


@pytest.mark.skipif(not _official_ready(), reason='E_RECHNUNG_OFFIZIELL=1, Java und Validator nötig')
@pytest.mark.parametrize('name', sorted(CASES))
@pytest.mark.parametrize('fmt', FORMATS)
def test_official_validator_accepts_every_case(name, fmt):
    from e_rechnung import official
    result = create(CASES[name], fmt)
    report = official.validate(result.content, Path(result.filename).suffix)
    assert report['valid'], report['messages']
    assert not [m for m in report['messages'] if m['level'] == 'fehler']
