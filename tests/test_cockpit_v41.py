"""V4.1 cockpit: content, isolation, escaping, Colab display, browser tab, Drive file."""
import json
import sys
import types
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

import crypto_bot_v41 as bot
import crypto_cockpit_v41 as cockpit_ui
from kraken_fake import kraken, no_wait  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
KURSE = {'bitcoin': 62000.0, 'ethereum': 2400.0, 'solana': 140.0}


def account(stop=58000.0):
    konto = bot.neues_konto()
    menge = 0.05
    kosten = menge * 60000.0 * (1 + bot.GEBUEHR)
    konto['guthaben'] -= kosten
    konto['tagesdatum'], konto['tagesstartwert'] = '2026-09-24', 10050.0
    konto['positionen']['bitcoin'] = dict(
        menge=menge, einstieg=60000.0, stop_loss=stop, atr_bei_einstieg=500.0,
        hoechster_schlusskurs=62500.0, gesamtkosten=kosten,
        kaufzeit='2026-09-23T08:00:00+00:00', datenquelle='Kraken')
    konto['transaktionen'] = [
        dict(zeit='2026-09-20T08:00:00+00:00', coin='solana', aktion='KAUF', menge=10.0, kurs=150.0,
             gebuehr=1.5, gesamtkosten=1501.5),
        dict(zeit='2026-09-21T09:00:00+00:00', coin='solana', aktion='VERKAUF', grund='STOP',
             menge=10.0, kurs=145.0, gebuehr=1.45, netto_erloes=1448.55, gewinn=-52.95),
        dict(zeit='2026-09-23T08:00:00+00:00', coin='bitcoin', aktion='KAUF', menge=menge,
             kurs=60000.0, gebuehr=3.0, gesamtkosten=kosten)]
    konto['letzter_ausstieg'] = {'solana': '2026-09-24T02:00:00+00:00'}
    return konto


def lauf(konto=None, kurse=KURSE, analyse=()):
    return dict(konto=konto if konto is not None else account(), kurse=kurse,
                kurse_zeit_utc=NOW.isoformat(), analyse=list(analyse),
                gestartet_utc=NOW.isoformat(), letzte_pruefung_utc=NOW.isoformat(),
                naechste_pruefung_utc=(NOW + timedelta(minutes=5)).isoformat(),
                durchlaeufe=3, api_fehler=0, letzter_fehler=None, cockpit_fehler=None)


def view(state, stufe='WARTET', ereignisse=()):
    data = cockpit_ui.snapshot(state, stufe, ereignisse, NOW)
    return data, cockpit_ui.render_html(data)


# ---------- isolation ----------

def test_cockpit_module_has_no_data_or_account_calls():
    source = open(cockpit_ui.__file__, encoding='utf-8').read()
    for forbidden in ('requests', 'kraken_anfrage', 'aktuelle_kurse', 'historische_kurse',
                      'konto_speichern', 'konto_laden', 'virtueller_', 'risk_gate'):
        assert forbidden not in source


def test_snapshot_does_not_change_the_account():
    konto = account()
    before = json.dumps(konto, sort_keys=True)
    view(lauf(konto))
    assert json.dumps(konto, sort_keys=True) == before


# ---------- content ----------

def test_numbers_match_the_bot():
    konto = account()
    data, _ = view(lauf(konto))
    assert data['kapital'] == pytest.approx(bot.kontowert_berechnen(konto, KURSE))
    assert data['offenes_risiko'] == pytest.approx(bot.offenes_risiko_berechnen(konto))
    p = data['positionen'][0]
    net = 0.05 * 62000.0 * (1 - bot.SLIPPAGE) * (1 - bot.GEBUEHR)
    assert p['ergebnis_nach_kosten'] == pytest.approx(net - konto['positionen']['bitcoin']['gesamtkosten'])
    assert data['realisiert'] == pytest.approx(-52.95)
    assert data['tagesgrenze'] == pytest.approx(10050.0 * (1 - bot.MAX_TAGESVERLUST))


def test_stop_below_entry_is_red_with_risk():
    data, html = view(lauf(account(stop=58000.0)))
    assert '<span class="bad">58.000,00 € (-3,33 %)</span>' in html
    assert 'Risiko bis Stop' in html


def test_stop_above_entry_is_green_and_shows_secured_result():
    data, html = view(lauf(account(stop=61000.0)))
    assert '<span class="ok">61.000,00 € (+1,67 %)</span>' in html
    assert data['positionen'][0]['ergebnis_am_stop'] > 0
    assert 'sichert +' in html


def test_triggered_stop_is_flagged():
    _, html = view(lauf(account(stop=62500.0)))
    assert 'Stop erreicht' in html


def test_closed_trades_pair_sale_with_its_purchase():
    data, html = view(lauf())
    assert data['abschluesse'] == [dict(
        coin='solana', menge=10.0, kaufzeit='2026-09-20T08:00:00+00:00',
        verkaufszeit='2026-09-21T09:00:00+00:00', kaufkurs=150.0, verkaufskurs=145.0,
        gewinn=-52.95, grund='STOP')]
    assert '20.09. 10:00 → 21.09. 11:00' in html      # Europe/Berlin, MESZ
    assert 'Trailing-Stop' in html


def test_analysis_shows_reasons_and_trading_pause():
    analyse = [dict(coin='solana', zeit=NOW.isoformat(), status='OK', signal='LONG', gruende=[],
                    entscheidung='Kein Kauf: 24 Stunden Handelspause', kurs=140.0, ma20=139.0,
                    ma50=135.0, ma200=120.0, atr=1.2, adx=31.0, kerze_utc=NOW.isoformat()),
               dict(coin='bitcoin', zeit=NOW.isoformat(), status='OK', signal='NO TRADE',
                    gruende=['kein Crossover'], entscheidung='Kein Einstieg', adx=12.0),
               dict(coin='ethereum', zeit=NOW.isoformat(), status='DATENFEHLER', signal=None,
                    gruende=[], entscheidung='Keine neuen Trades: Lücke')]
    data, html = view(lauf(analyse=analyse))
    assert data['analyse'][0]['handelspause_bis'] == '2026-09-25T02:00:00+00:00'
    assert 'Handelspause bis 25.09. 04:00' in html
    assert 'kein Crossover' in html and 'Datenfehler' in html and '<span class="ok">LONG</span>' in html


def test_daily_limit_blocks_new_entries_in_the_view():
    konto = account()
    konto['tageslimit_erreicht'] = True
    _, html = view(lauf(konto))
    assert 'Neue Käufe gesperrt: Tagesverlustlimit erreicht' in html


def test_view_before_first_price_check():
    data, html = view(lauf(konto=None, kurse=None), stufe='API_FEHLER')
    assert data['bewertet'] is False
    assert 'noch nicht bewertet' in html and 'Kraken nicht erreichbar' in html


def test_failed_run_shows_the_error():
    state = lauf()
    state['letzter_fehler'] = 'KeyError: x'
    _, html = view(state, stufe='FEHLER')
    assert 'Programmfehler' in html and 'Letzter Fehler: KeyError: x' in html
    assert 'data-quiet="1"' in html


def test_html_escapes_external_text():
    state = lauf()
    state['letzter_fehler'] = '<script>alert(1)</script>'
    events = [dict(zeit=NOW.isoformat(), art='API_FEHLER', coin=None, text='<img src=x onerror=alert(1)>')]
    _, html = view(state, 'API_FEHLER', events)
    assert '<script>alert' not in html and '<img src=x' not in html
    assert '&lt;script&gt;' in html and '&lt;img src=x' in html


def test_full_offline_run_renders(kraken, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'cockpit_latest.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'cockpit_latest.html').read_text()
    assert 'Bitcoin' in page and 'KAUF' in page and 'echte Orders: 0' in page


# ---------- browser page, Drive file, Colab display, server ----------

def test_browser_page_refreshes_and_warns_when_stalled():
    _, html = view(lauf())
    page = cockpit_ui.browser_page(html)
    assert '<meta http-equiv="refresh" content="15">' in page
    assert 'data-stall-min="11"' in page and 'Bot läuft evtl. nicht mehr' in page
    assert 'data-quiet="0"' in page


def test_stopped_bot_does_not_raise_a_stall_warning():
    _, html = view(lauf(), stufe='GESTOPPT')
    assert 'data-quiet="1"' in html


def test_latest_file_is_rewritten_on_every_update(tmp_path):
    target = tmp_path / 'Trading_Bot' / 'cockpit_latest.html'
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=target)
    cockpit.update(lauf(), 'WARTET', now=NOW)
    first = target.read_text()
    cockpit.update(lauf(), 'GESTOPPT', now=NOW + timedelta(minutes=5))
    assert target.read_text() != first and 'Bot gestoppt' in target.read_text()
    assert 'http-equiv="refresh"' in first
    assert not list(target.parent.glob('*.incomplete'))


def test_drive_write_error_does_not_switch_the_cockpit_off(tmp_path):
    blocker = tmp_path / 'datei'
    blocker.write_text('kein Ordner')
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=blocker / 'cockpit_latest.html')
    assert cockpit.update(lauf(), 'WARTET', now=NOW) is not None
    assert cockpit.update(lauf(), 'WARTET', now=NOW) is not None
    assert cockpit.write_errors == 2
    assert 'Gesamtvermögen' in cockpit.latest_browser_page


def test_colab_display_is_created_once_and_updated_in_place(monkeypatch):
    shown = []

    class Handle:
        def update(self, obj):
            shown.append(('update', obj.data))

    def display(obj, display_id=False):
        assert display_id is True
        shown.append(('display', obj.data))
        return Handle()

    fake = types.ModuleType('IPython.display')
    fake.HTML = lambda data: types.SimpleNamespace(data=data)
    fake.display = display
    monkeypatch.setitem(sys.modules, 'IPython', types.ModuleType('IPython'))
    monkeypatch.setitem(sys.modules, 'IPython.display', fake)
    cockpit = cockpit_ui.Cockpit(display_enabled=True)
    cockpit.show_placeholder()
    cockpit.update(lauf(), 'WARTET', now=NOW)
    cockpit.update(lauf(), 'WARTET', now=NOW)
    assert [kind for kind, _ in shown] == ['display', 'update', 'update']
    assert 'Warte auf die erste Kursprüfung' in shown[0][1]
    # The Colab view has no reload tag; only the browser page reloads itself.
    assert 'http-equiv="refresh"' not in shown[1][1]


def fetch(port, path):
    return urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5)


def test_browser_link_serves_newest_view_read_only():
    cockpit = cockpit_ui.Cockpit(display_enabled=False)
    server = cockpit_ui.serve_cockpit(cockpit, port=18766)
    try:
        port = server.server_address[1]
        assert 'Warte auf die erste Kursprüfung' in fetch(port, '/').read().decode()
        cockpit.update(lauf(), 'WARTET', now=NOW)
        answer = fetch(port, '/?x=1')
        assert answer.headers['Cache-Control'] == 'no-store'
        assert 'Gesamtvermögen' in answer.read().decode()
        for path in ('/konto_v3_trendfolge.json', '/../etc/passwd', '/snapshot.json'):
            with pytest.raises(urllib.error.HTTPError) as error:
                fetch(port, path)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_second_server_uses_next_free_port():
    cockpit = cockpit_ui.Cockpit(display_enabled=False)
    first = cockpit_ui.serve_cockpit(cockpit, port=18786)
    second = cockpit_ui.serve_cockpit(cockpit, port=18786)
    try:
        assert second.server_address[1] == first.server_address[1] + 1
    finally:
        for server in (first, second):
            server.shutdown()
            server.server_close()


def test_open_browser_view_uses_colab_port_window(monkeypatch):
    calls = []
    output = types.ModuleType('google.colab.output')
    output.serve_kernel_port_as_window = lambda port, path, anchor_text: calls.append((port, path, anchor_text))
    colab = types.ModuleType('google.colab')
    colab.output = output
    monkeypatch.setitem(sys.modules, 'google', types.ModuleType('google'))
    monkeypatch.setitem(sys.modules, 'google.colab', colab)
    monkeypatch.setitem(sys.modules, 'google.colab.output', output)
    server = cockpit_ui.open_browser_view(cockpit_ui.Cockpit(display_enabled=False), port=18806)
    try:
        assert calls == [(server.server_address[1], '/', 'Cockpit im Browser öffnen')]
    finally:
        server.shutdown()
        server.server_close()
