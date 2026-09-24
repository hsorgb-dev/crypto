"""V5: Alpaca as read-only data source, 10 coins, USD budget; strategy unchanged from V4.1."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_bot_v41 as v41
import crypto_bot_v5 as bot
import crypto_cockpit_v5 as cockpit_ui
from alpaca.common.exceptions import APIError
from alpaca_fake import ReadOnlyBroker, alpaca, no_wait  # noqa: F401  (fixture)

ORDER_CALLS = ('submit_order', 'close_position', 'close_all_positions', 'cancel_order',
               'replace_order', 'TradingClient(', 'OrderRequest')


def saved_account():
    return json.loads(bot.KONTO_DATEI.read_text())


# ---------- no orders, strategy unchanged ----------

@pytest.mark.parametrize('module', [bot, cockpit_ui])
def test_modules_contain_no_order_calls(module):
    source = open(module.__file__, encoding='utf-8').read()
    for forbidden in ORDER_CALLS:
        assert forbidden not in source


def test_strategy_and_risk_settings_match_v41():
    for name in ('TREND_FILTER_PERIODE', 'ADX_PERIODE', 'ADX_MINDESTWERT', 'ATR_PERIODE',
                 'ATR_STOP_MULTIPLIKATOR', 'ATR_TRAILING_MULTIPLIKATOR', 'MIN_ERFORDERLICHE_KERZEN',
                 'MAX_RISIKO_PRO_TRADE', 'MAX_GESAMTRISIKO', 'MAX_POSITIONSANTEIL',
                 'MAX_OFFENE_POSITIONEN', 'MAX_TAGESVERLUST', 'GEBUEHR', 'SLIPPAGE',
                 'HANDELSPAUSE', 'INTERVALL', 'STARTKAPITAL'):
        assert getattr(bot, name) == getattr(v41, name), name
    for name in ('risk_gate', 'trailing_stop_berechnen', 'atr_berechnen', 'adx_berechnen',
                 'offenes_risiko_berechnen', 'virtueller_verkauf', 'tageslimit_pruefen'):
        strip = lambda f: f.__code__.co_code
        assert strip(getattr(bot, name)) == strip(getattr(v41, name)), name


def test_ten_coins_in_usd():
    assert [i['symbol'] for i in bot.COINS.values()] == [
        'BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'DOGE/USD',
        'ADA/USD', 'AVAX/USD', 'LINK/USD', 'DOT/USD', 'LTC/USD']
    assert bot.usd(1234.5) == '1.234,50 USD'


def test_smoke_test_passes():
    bot.smoke_test()


# ---------- start ----------

def test_start_reads_account_once_and_skips_unlisted_coins(alpaca, tmp_path, monkeypatch):
    alpaca.unlisted.add('ADA/USD')
    broker = ReadOnlyBroker()
    monkeypatch.setattr(bot, 'hauptschleife', lambda cockpit=None: 'lauf')
    assert bot.start(alpaca, broker=broker, ordner=tmp_path / 'tb', drive_verbinden=False) == 'lauf'
    assert broker.calls == 1
    assert bot.ALPACA_KONTO['equity'] == 100000.0
    assert 'cardano' not in bot.HANDELSPAARE and len(bot.HANDELSPAARE) == 9
    assert saved_account()['startkapital'] == 10000.0
    assert bot.KONTO_DATEI.name == 'konto_v5_alpaca_usd.json'


def test_start_refuses_open_position_without_data(alpaca, tmp_path, monkeypatch):
    bot.speicherorte_einrichten(tmp_path / 'tb')
    konto = bot.neues_konto()
    konto['positionen']['cardano'] = dict(menge=100.0, einstieg=0.4, stop_loss=0.38,
        atr_bei_einstieg=0.01, hoechster_schlusskurs=0.4, gesamtkosten=40.04)
    bot.konto_speichern(konto)
    alpaca.unlisted.add('ADA/USD')
    monkeypatch.setattr(bot, 'hauptschleife', lambda cockpit=None: None)
    with pytest.raises(RuntimeError, match='cardano'):
        bot.start(alpaca, ordner=tmp_path / 'tb', drive_verbinden=False)


# ---------- prices ----------

def test_price_is_mid_quote(alpaca):
    kurse = bot.aktuelle_kurse()
    mid = alpaca.last_close('BTC/USD') * 1.001
    assert kurse['bitcoin'] == pytest.approx(mid)


def test_stale_quote_drops_only_that_coin(alpaca):
    alpaca.stale.add('DOT/USD')
    kurse = bot.aktuelle_kurse(bot.neues_konto())
    assert 'polkadot' not in kurse and len(kurse) == 9
    assert 'veraltet' in bot.KURSFEHLER['polkadot']


def test_stale_quote_of_open_position_skips_the_pass(alpaca):
    alpaca.stale.add('DOT/USD')
    konto = bot.neues_konto()
    konto['positionen']['polkadot'] = dict(menge=10.0, einstieg=4.5, stop_loss=4.2,
        atr_bei_einstieg=0.1, hoechster_schlusskurs=4.5, gesamtkosten=45.05)
    bot.konto_speichern(konto)
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    assert 'Kein Kurs für offene Position' in lauf['letzter_fehler']
    assert saved_account()['positionen'].keys() == {'polkadot'}


def test_alpaca_api_error_does_not_stop_the_bot(alpaca):
    alpaca.fail = APIError('{"message": "too many requests"}')
    lauf = bot.hauptschleife(durchlaeufe=2, warten=no_wait)
    assert lauf['api_fehler'] == 2 and lauf['durchlaeufe'] == 2


# ---------- hourly candles and hours without trades ----------

def test_quiet_hours_are_filled_flat_and_marked(alpaca):
    alpaca.quiet_hours['XRP/USD'] = {5, 6}
    df = bot.historische_kurse('xrp')
    assert (df.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
    filled = df[df['ohne_handel']]
    assert len(filled) == 2
    assert (filled['volume'] == 0).all()
    assert (filled['open'] == filled['close']).all() and (filled['high'] == filled['low']).all()
    bot.marktdaten_pruefen(df, 'xrp')            # 2 quiet hours are fine


def test_too_many_quiet_hours_block_new_entries(alpaca):
    alpaca.quiet_hours['XRP/USD'] = {3, 10, 20, 30, 40, 45}
    df = bot.historische_kurse('xrp')
    with pytest.raises(ValueError, match='Zu wenig Handel'):
        bot.marktdaten_pruefen(df, 'xrp')


def test_long_quiet_stretch_blocks_new_entries(alpaca):
    alpaca.quiet_hours['LTC/USD'] = {4, 5, 6, 7}
    df = bot.historische_kurse('litecoin')
    with pytest.raises(ValueError, match='am Stück'):
        bot.marktdaten_pruefen(df, 'litecoin')


def test_quiet_last_hour_is_filled_and_a_late_bar_replaces_it(alpaca):
    alpaca.quiet_hours['AVAX/USD'] = {1}
    df = bot.historische_kurse('avalanche')
    assert bool(df['ohne_handel'].iloc[-1])
    alpaca.quiet_hours['AVAX/USD'] = set()
    df = bot.historische_kurse('avalanche')
    assert not df['ohne_handel'].any()
    assert bot.kurse_laden('avalanche')['ohne_handel'].dtype == bool


def test_second_fetch_only_asks_for_new_hours(alpaca):
    bot.historische_kurse('bitcoin')
    bot.historische_kurse('bitcoin')
    first, second = [c for c in alpaca.calls if c[0] == 'bars']
    assert second[2] > first[2]


# ---------- full pass ----------

def test_uptrend_pass_respects_position_and_risk_limits(alpaca):
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    konto = saved_account()
    assert 1 <= len(konto['positionen']) <= bot.MAX_OFFENE_POSITIONEN
    kapital = bot.kontowert_berechnen(konto, lauf['kurse'])
    assert bot.offenes_risiko_berechnen(konto) <= kapital * bot.MAX_GESAMTRISIKO + 1e-6
    assert len(lauf['analyse']) == 10
    assert all(t['datenquelle'] == 'Alpaca' for t in konto['transaktionen'])


def test_cockpit_changes_nothing_but_the_view(tmp_path, alpaca, monkeypatch):
    from alpaca_fake import FakeAlpacaData

    def run(cockpit, folder):
        fake = FakeAlpacaData()
        monkeypatch.setattr(bot, 'DATENQUELLE', fake)
        bot.speicherorte_einrichten(folder)
        bot.EREIGNISSE.clear()
        bot.hauptschleife(cockpit=cockpit, durchlaeufe=2, warten=no_wait)
        konto = saved_account()
        for t in konto['transaktionen']:
            t.pop('zeit')
        for p in konto['positionen'].values():
            p.pop('kaufzeit')
        konto.pop('erstellt_am')
        return konto, [c[:2] for c in fake.calls]

    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'view.html')
    assert run(cockpit, tmp_path / 'with') == run(None, tmp_path / 'without')
    assert 'Alpaca-Orders: 0' in (tmp_path / 'view.html').read_text()


def test_small_prices_keep_enough_decimals():
    assert bot.usd(0.18734) == '0,1873 USD'
    assert bot.usd(0.004567) == '0,004567 USD'
    assert bot.usd(-12.5) == '-12,50 USD'
    assert bot.usd(0) == '0,00 USD'


def test_routine_rejections_do_not_flood_the_event_log(alpaca):
    bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    kinds = [(e['art'], e['text']) for e in bot.EREIGNISSE]
    assert ('KEIN_KAUF', 'Maximale Positionszahl erreicht') not in kinds
    assert sum(1 for art, _ in kinds if art == 'KAUF') == bot.MAX_OFFENE_POSITIONEN
