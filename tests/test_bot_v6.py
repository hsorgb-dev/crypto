"""V6: strategies A (pullback) and B (squeeze) on 1h + 15m Alpaca bars, shared risk gate, no orders."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import crypto_bot_v6 as bot
import crypto_cockpit_v6 as cockpit_ui
from alpaca_fake_v6 import alpaca6, frame, no_wait, uptrend  # noqa: F401  (fixture)

ORDER_CALLS = ('submit_order', 'close_position', 'close_all_positions', 'cancel_order',
               'replace_order', 'TradingClient(', 'OrderRequest')
T0 = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def closed(df):
    """Closed candles as the bot stores them (running row dropped, ohne_handel column)."""
    df = df.iloc[:-1].copy()
    df['ohne_handel'] = False
    return df


def hourly_uptrend(n=301):
    return closed(frame(uptrend(100, n)))


def with_pullback(raw, back=3):
    """Let the candle `back` hours before the end dip its low below the EMA 20."""
    ema = bot.indikatoren_1h(raw)[f'EMA{bot.EMA_KURZ}']
    raw = raw.copy()
    raw.iloc[-back, raw.columns.get_loc('low')] = ema.iloc[-back] * 0.999
    return raw


def squeeze_closes(n=301, calm=25, amplitude=0.05):
    """Swinging prices whose last `calm` hours swing with `amplitude` (small = squeeze)."""
    i = np.arange(n)
    noisy = 100 + 0.01 * i + 3 * np.sin(i * 1.3)
    last = 100 + 0.01 * i + amplitude * np.sin(i * 1.3)
    return np.where(i < n - calm, noisy, last)


def signal_frame(level, jump=1.005, volume=20.0, n=60):
    """15m candles flat at `level`; the last closed one closes at level*jump with `volume`."""
    closes = np.full(n, level)
    closes[-2] = level * jump
    volumes = np.full(n, 10.0)
    volumes[-2] = volume
    return frame(closes, '15min', spread=0.001, volumes=volumes)


# ---------- no orders, fixed parameters ----------

@pytest.mark.parametrize('module', [bot, cockpit_ui])
def test_modules_contain_no_order_calls(module):
    source = open(module.__file__, encoding='utf-8').read()
    for forbidden in ORDER_CALLS:
        assert forbidden not in source


def test_parameters_are_the_specified_test_values():
    assert (bot.EMA_KURZ, bot.EMA_MITTEL, bot.PULLBACK_KERZEN, bot.A_AUSBRUCH_KERZEN,
            bot.A_VOLUMEN_FAKTOR) == (20, 50, 6, 3, 1.0)
    assert (bot.BB_PERIODE, bot.BB_STDABW, bot.SQUEEZE_REFERENZ, bot.SQUEEZE_QUANTIL,
            bot.SPANNE_KERZEN, bot.SQUEEZE_GUELTIG_STUNDEN, bot.B_VOLUMEN_FAKTOR) == (20, 2.0, 120, 0.2, 12, 12, 1.5)
    assert (bot.MAX_RISIKO_PRO_TRADE, bot.MAX_GESAMTRISIKO, bot.MAX_OFFENE_POSITIONEN) == (0.01, 0.03, 3)


def test_smoke_test_passes():
    bot.smoke_test()


# ---------- strategy A: checks 1 and 2 ----------

def test_pullback_to_ema20_is_detected():
    df = bot.indikatoren_1h(with_pullback(hourly_uptrend()))
    assert bot.marktregime(df.iloc[-1]) == 'AUFWAERTSTREND'
    pullback, grund = bot.pullback_pruefen(df)
    assert grund == 'Pullback erkannt'
    assert pullback['id'] == df.index[-3].isoformat()
    assert pullback['tief'] == pytest.approx(df['low'].iloc[-6:].min())


def test_no_pullback_without_touching_ema20():
    pullback, grund = bot.pullback_pruefen(bot.indikatoren_1h(hourly_uptrend()))
    assert pullback is None and 'Rücksetzer' in grund


def test_pullback_that_closed_below_ema50_is_rejected():
    raw = with_pullback(hourly_uptrend())
    ema50 = bot.indikatoren_1h(raw)[f'EMA{bot.EMA_MITTEL}']
    raw.iloc[-4, raw.columns.get_loc('close')] = ema50.iloc[-4] * 0.99
    pullback, grund = bot.pullback_pruefen(bot.indikatoren_1h(raw))
    assert pullback is None and 'EMA 50' in grund


def test_no_pullback_entries_in_a_downtrend():
    df = bot.indikatoren_1h(closed(frame(uptrend(300, 301, 0.998))))
    assert bot.marktregime(df.iloc[-1]) == 'ABWAERTSTREND'
    assert bot.pullback_pruefen(df)[0] is None


def test_pullback_window_expires_and_is_used_once():
    df = bot.indikatoren_1h(with_pullback(hourly_uptrend()))
    a = bot.zustand_a_aktualisieren(None, df, T0)
    assert a['status'] == 'BEREIT' and a['bis'] == (T0 + timedelta(hours=4)).isoformat()
    later = bot.zustand_a_aktualisieren(a, df, T0 + timedelta(hours=4))
    assert later['status'] == 'ABGELAUFEN' and later['seit'] == a['seit']
    a['benutzt'] = [a['id']]
    assert bot.zustand_a_aktualisieren(a, df, T0 + timedelta(hours=1))['status'] == 'BENUTZT'


# ---------- strategy B: squeeze and range ----------

def test_squeeze_uses_only_previous_120_widths():
    df = bot.indikatoren_1h(closed(frame(squeeze_closes())))
    ok, info, grund = bot.squeeze_pruefen(df)
    assert ok, grund
    assert info['schwelle'] == pytest.approx(df['BBW'].iloc[-121:-1].quantile(0.2))
    assert info['bbw'] == pytest.approx(df['BBW'].iloc[-1])


def test_no_squeeze_in_normal_volatility():
    df = bot.indikatoren_1h(closed(frame(squeeze_closes(amplitude=6))))
    assert bot.squeeze_pruefen(df)[0] is False


def test_hour_without_trades_cannot_fake_a_squeeze():
    raw = closed(frame(squeeze_closes()))
    raw.iloc[-5, raw.columns.get_loc('ohne_handel')] = True
    ok, _, grund = bot.squeeze_pruefen(bot.indikatoren_1h(raw))
    assert not ok and 'ohne Handel' in grund


def test_squeeze_range_is_fixed_expires_and_is_not_reused():
    squeeze = bot.indikatoren_1h(closed(frame(squeeze_closes())))
    normal = bot.indikatoren_1h(closed(frame(squeeze_closes(amplitude=6))))
    b = bot.zustand_b_aktualisieren(None, squeeze, T0)
    assert b['status'] == 'AKTIV'
    assert b['oben'] == pytest.approx(squeeze['high'].iloc[-12:].max())
    assert b['unten'] == pytest.approx(squeeze['low'].iloc[-12:].min())
    moved = squeeze.copy()
    moved['high'] *= 1.01
    same = bot.zustand_b_aktualisieren(b, moved, T0 + timedelta(hours=1))
    assert same['status'] == 'AKTIV' and same['oben'] == b['oben']
    expired = bot.zustand_b_aktualisieren(same, squeeze, T0 + timedelta(hours=12))
    assert expired['status'] == 'ABGELAUFEN'
    # Still the same compression: no new phase
    assert bot.zustand_b_aktualisieren(expired, squeeze, T0 + timedelta(hours=13))['status'] == 'ABGELAUFEN'
    ended = bot.zustand_b_aktualisieren(expired, normal, T0 + timedelta(hours=14))
    assert ended['status'] == 'INAKTIV'
    again = bot.zustand_b_aktualisieren(ended, squeeze, T0 + timedelta(hours=15))
    assert again['status'] == 'AKTIV' and again['seit'] == (T0 + timedelta(hours=15)).isoformat()


# ---------- 15-minute signals ----------

def fifteen(level=100.0, **kwargs):
    df = closed(signal_frame(level, **kwargs))
    return df


def test_signal_a_needs_breakout_and_volume():
    a = {'seit': '2000-01-01T00:00:00+00:00', 'bis': '2100-01-01T00:00:00+00:00'}
    assert bot.signal_a_pruefen(fifteen(volume=10.0), a)[1] == 'Signal A'
    assert bot.signal_a_pruefen(fifteen(volume=9.0), a)[1] == 'Volumen zu gering'
    assert 'Hoch' in bot.signal_a_pruefen(fifteen(jump=1.0), a)[1]


def test_signal_candle_must_close_after_the_pullback_was_confirmed():
    df = fifteen()
    end = df.index[-1] + pd.Timedelta(minutes=15)
    a = {'seit': end.isoformat(), 'bis': '2100-01-01T00:00:00+00:00'}
    assert bot.signal_a_pruefen(df, a)[1] == 'noch keine neue 15-Min-Kerze'


def test_signal_b_needs_close_above_range_volume_and_no_overextension():
    b = {'seit': '2000-01-01T00:00:00+00:00', 'oben': 100.2, 'unten': 99.0}
    assert bot.signal_b_pruefen(fifteen(volume=15.0), b, atr=1.0)[1] == 'Signal B'
    assert bot.signal_b_pruefen(fifteen(volume=14.0), b, atr=1.0)[1] == 'Ausbruchsvolumen zu gering'
    assert bot.signal_b_pruefen(fifteen(jump=1.001, volume=20.0), b, atr=1.0)[1] == 'kein Schluss über der Spanne'
    assert bot.signal_b_pruefen(fifteen(jump=1.02, volume=20.0), b, atr=1.0)[1] == 'Ausbruch schon zu weit gelaufen'


def test_quiet_15m_candles_block_volume_signals():
    df = fifteen(volume=20.0)
    df.iloc[-6:-3, df.columns.get_loc('ohne_handel')] = True
    a = {'seit': '2000-01-01T00:00:00+00:00', 'bis': '2100-01-01T00:00:00+00:00'}
    assert bot.signal_a_pruefen(df, a)[1] == 'zu wenig Handel in den 15-Min-Kerzen'


# ---------- risk gate additions ----------

def test_risk_gate_rejects_wide_spread_and_stop_above_entry():
    konto = bot.neues_konto()
    kurse = {'bitcoin': 100.0}
    assert bot.risk_gate(konto, 'bitcoin', kurse, 1.0, spread=0.002)[1].startswith('Spread zu hoch')
    assert bot.risk_gate(konto, 'bitcoin', kurse, 1.0, stop_vorgabe=lambda e: e + 1,
                         spread=0.0005)[1] == 'Stop liegt nicht unter dem Einstieg'
    pruefung, grund = bot.risk_gate(konto, 'bitcoin', kurse, 1.0, stop_vorgabe=lambda e: 97.0, spread=0.0005)
    assert grund == 'Freigegeben' and pruefung['stop_loss'] == 97.0


def test_takt_is_aligned_to_five_minutes():
    at = lambda m, s: datetime(2026, 9, 24, 10, m, s, tzinfo=timezone.utc)
    assert bot.sekunden_bis_naechster_takt(at(3, 0)) == 140
    assert bot.sekunden_bis_naechster_takt(at(4, 50)) == 30
    assert bot.sekunden_bis_naechster_takt(at(5, 10)) == 10


# ---------- full passes with the offline Alpaca ----------

def saved_account():
    return json.loads(bot.KONTO_DATEI.read_text())


def test_first_pass_always_analyses_all_coins(alpaca6):
    konto = bot.neues_konto()
    konto['letzte_analyse'] = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')
    bot.konto_speichern(konto)
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    assert len(lauf['marktlage']) == 10
    assert all(e.get('regime') for e in lauf['marktlage'].values())
    assert json.loads(bot.STRATEGIE_DATEI.read_text()).keys() == set(bot.COINS)


def test_strategy_a_end_to_end(alpaca6):
    raw_1h = with_pullback(closed(frame(uptrend(100, 321))))
    running = frame(uptrend(100, 321)).iloc[[-1]].copy()
    alpaca6.frames[('BTC/USD', 'h')] = pd.concat([raw_1h.drop(columns='ohne_handel'), running])
    df = bot.indikatoren_1h(raw_1h)
    pullback, _ = bot.pullback_pruefen(df)
    level = float(raw_1h['close'].iloc[-1])
    alpaca6.frames[('BTC/USD', '15min')] = signal_frame(level, jump=1.004, volume=12.0)
    seit = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    bot.STRATEGIE_DATEI.write_text(json.dumps(
        {'bitcoin': {'a': {'status': 'BEREIT', 'seit': seit, 'benutzt': [], **pullback}}}))

    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    position = saved_account()['positionen']['bitcoin']
    assert position['strategie'] == 'A' and position['pullback_id'] == pullback['id']
    atr = float(df['ATR'].iloc[-1])
    assert position['stop_loss'] == pytest.approx(pullback['tief'] - 0.5 * atr)
    assert lauf['strategien']['bitcoin']['a']['status'] == 'BENUTZT'
    assert 'virtuell gekauft' in lauf['marktlage']['bitcoin']['signal']['text']


def test_strategy_b_end_to_end(alpaca6):
    closes = squeeze_closes(n=322)
    alpaca6.frames[('ETH/USD', 'h')] = frame(closes)
    df = bot.indikatoren_1h(closed(frame(closes)))
    oben = float(df['high'].iloc[-12:].max())
    unten = float(df['low'].iloc[-12:].min())
    alpaca6.frames[('ETH/USD', '15min')] = signal_frame(oben / 1.002, jump=1.004, volume=20.0)
    jetzt = datetime.now(timezone.utc)
    bot.STRATEGIE_DATEI.write_text(json.dumps({'ethereum': {'b': {
        'status': 'AKTIV', 'oben': oben, 'unten': unten,
        'seit': (jetzt - timedelta(hours=1)).isoformat(), 'bis': (jetzt + timedelta(hours=11)).isoformat()}}}))

    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    position = saved_account()['positionen']['ethereum']
    assert position['strategie'] == 'B' and position['spanne_oben'] == oben
    assert position['stop_loss'] >= (oben + unten) / 2 - 1e-9
    assert lauf['strategien']['ethereum']['b']['status'] == 'BENUTZT'


def test_exits_a_trend_break_and_b_false_breakout(alpaca6):
    konto = bot.neues_konto()
    kauf = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    for coin, strategie, extra in (('bitcoin', 'A', {}), ('ethereum', 'B', {'spanne_oben': 10 ** 6})):
        konto['positionen'][coin] = dict(menge=1.0, einstieg=100.0, stop_loss=50.0, atr_bei_einstieg=1.0,
                                         hoechster_schlusskurs=100.0, gesamtkosten=100.1, kaufzeit=kauf,
                                         strategie=strategie, **extra)
    # A: last hourly close far below EMA 50
    closes = uptrend(100, 321)
    closes[-2] = closes[-60]
    alpaca6.frames[('BTC/USD', 'h')] = frame(closes)
    bot.konto_speichern(konto)
    bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    reasons = {t['coin']: t['grund'] for t in saved_account()['transaktionen'] if t['aktion'] == 'VERKAUF'}
    assert reasons == {'bitcoin': 'TRENDBRUCH', 'ethereum': 'FEHLAUSBRUCH'}


def test_b_time_stop_after_24_hours_without_progress(alpaca6):
    konto = bot.neues_konto()
    kurs = float(alpaca6.get('ETH/USD', 'h')['close'].iloc[-2])
    konto['positionen']['ethereum'] = dict(
        menge=1.0, einstieg=kurs, stop_loss=kurs * 0.5, atr_bei_einstieg=kurs, hoechster_schlusskurs=kurs,
        gesamtkosten=kurs, kaufzeit=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        strategie='B', spanne_oben=0.0)
    bot.konto_speichern(konto)
    bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    assert [t['grund'] for t in saved_account()['transaktionen'] if t['aktion'] == 'VERKAUF'] == ['ZEITSTOP']


def test_cockpit_shows_strategy_states(alpaca6, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v6.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'v6.html').read_text()
    assert 'Marktlage und Strategien' in page and 'A · Pullback' in page and 'B · Squeeze' in page
    assert 'vorläufig definiert' in page and 'Alpaca-Orders: 0' in page
