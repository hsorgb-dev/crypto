"""V5 cockpit: USD, Alpaca snapshot never mixed with the virtual account, missing prices, 10 coins.

The shared view mechanics (escaping, browser page, server, Drive file) are covered for V4.1."""
from datetime import datetime, timezone

import crypto_bot_v5 as bot
import crypto_cockpit_v5 as cockpit_ui
from alpaca_fake import alpaca, no_wait  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def state(alpaca_konto=None, kursfehler=None):
    konto = bot.neues_konto()
    konto['positionen']['xrp'] = dict(menge=1000.0, einstieg=0.60, stop_loss=0.58,
        atr_bei_einstieg=0.01, hoechster_schlusskurs=0.61, gesamtkosten=600.6,
        kaufzeit=NOW.isoformat(), datenquelle='Alpaca')
    konto['guthaben'] -= 600.6
    return dict(konto=konto, kurse={'xrp': 0.62}, kurse_zeit_utc=NOW.isoformat(), analyse=[],
                gestartet_utc=NOW.isoformat(), letzte_pruefung_utc=NOW.isoformat(),
                naechste_pruefung_utc=NOW.isoformat(), durchlaeufe=1, api_fehler=0,
                letzter_fehler=None, cockpit_fehler=None, alpaca_konto=alpaca_konto,
                kursfehler=kursfehler or {})


def render(lauf, stufe='WARTET'):
    data = cockpit_ui.snapshot(lauf, stufe, [], NOW)
    return data, cockpit_ui.render_html(data)


def test_values_in_usd_and_no_orders():
    _, html = render(state())
    assert '0,6000 USD' in html and '€' not in html
    assert 'Alpaca-Orders: 0' in html and 'Kraken' not in html


def test_alpaca_snapshot_is_shown_but_never_added():
    data, html = render(state(alpaca_konto={'equity': 100000.0, 'waehrung': 'USD'}))
    assert 'Start-Snapshot des Alpaca-Paper-Kontos: 100.000,00 USD' in html
    assert 'niemals mit dem virtuellen Bot-Konto verrechnen' in html
    assert data['kapital'] < 11000


def test_coins_without_price_are_named():
    _, html = render(state(kursfehler={'polkadot': 'Alpaca-Quote veraltet (30 Min.): DOT/USD'}))
    assert 'Ohne aktuellen Kurs: Polkadot: Alpaca-Quote veraltet (30 Min.): DOT/USD' in html


def test_data_error_status():
    _, html = render(state(), 'API_FEHLER')
    assert 'Alpaca-Daten nicht verfügbar' in html


def test_full_offline_run_shows_ten_coins_and_quiet_hours(alpaca, tmp_path):
    alpaca.quiet_hours['DOGE/USD'] = {7}
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'cockpit_v5_latest.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'cockpit_v5_latest.html').read_text()
    for coin in bot.COINS.values():
        assert coin['name'] in page
    assert '1/50 h ohne Handel' in page
    assert '10 von 10 Coins' in page


def test_browser_server_uses_its_own_port():
    server = cockpit_ui.serve_cockpit(cockpit_ui.Cockpit(display_enabled=False), port=18826)
    try:
        assert server.server_address[1] >= 18826
    finally:
        server.shutdown()
        server.server_close()
    assert cockpit_ui.open_browser_view.__defaults__ == (8767,)
