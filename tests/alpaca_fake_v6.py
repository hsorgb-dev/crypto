"""Offline Alpaca for V6: hourly and 15-minute bars from given price series, ending now."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import crypto_bot_v6 as bot

FREQ = {'1Hour': 'h', '15Min': '15min'}


def frame(closes, freq='h', spread=0.004, volumes=None, end=None):
    """OHLCV frame whose LAST row is the still running period (the bot must ignore it)."""
    closes = np.asarray(closes, dtype=float)
    end = end or pd.Timestamp.now(tz='UTC').floor(freq)
    index = pd.date_range(end=end, periods=len(closes), freq=freq, tz='UTC')
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        'open': opens,
        'high': np.maximum(opens, closes) * (1 + spread / 2),
        'low': np.minimum(opens, closes) * (1 - spread / 2),
        'close': closes,
        'volume': np.full(len(closes), 10.0) if volumes is None else np.asarray(volumes, float),
    }, index=index)


def uptrend(start, n, step=1.002):
    return start * step ** np.arange(n)


class FakeAlpacaV6:
    def __init__(self):
        self.frames = {}          # (symbol, 'h' | '15min') -> DataFrame
        self.calls = []
        self.stale = set()
        self.fail = None

    def get(self, symbol, freq):
        if (symbol, freq) not in self.frames:
            start = 100.0 + 10 * len(symbol)
            n = 320 if freq == 'h' else 400
            step = 1.002 if freq == 'h' else 1.0005
            self.frames[(symbol, freq)] = frame(uptrend(start, n, step), freq)
        return self.frames[(symbol, freq)]

    def get_crypto_bars(self, request):
        freq = FREQ[str(request.timeframe)]
        self.calls.append(('bars', request.symbol_or_symbols, freq))
        if self.fail:
            raise self.fail
        df = self.get(request.symbol_or_symbols, freq)
        start = pd.Timestamp(request.start)
        start = start.tz_localize('UTC') if start.tzinfo is None else start
        rows = [SimpleNamespace(timestamp=t.to_pydatetime(), open=r.open, high=r.high, low=r.low,
                                close=r.close, volume=r.volume)
                for t, r in df[df.index >= start].iterrows() if r.volume > 0]
        return SimpleNamespace(data={request.symbol_or_symbols: rows})

    def get_crypto_latest_quote(self, request):
        self.calls.append(('quote', tuple(request.symbol_or_symbols)))
        if self.fail:
            raise self.fail
        now = datetime.now(timezone.utc)
        quotes = {}
        for symbol in request.symbol_or_symbols:
            mid = float(self.get(symbol, '15min')['close'].iloc[-2])
            quotes[symbol] = SimpleNamespace(
                bid_price=mid * 0.9998, ask_price=mid * 1.0002,
                timestamp=now - (timedelta(minutes=30) if symbol in self.stale else timedelta(seconds=2)))
        return quotes


@pytest.fixture
def alpaca6(tmp_path, monkeypatch):
    fake = FakeAlpacaV6()
    monkeypatch.setattr(bot, 'DATENQUELLE', fake)
    monkeypatch.setattr(bot, 'HANDELSPAARE', {c: i['symbol'] for c, i in bot.COINS.items()})
    monkeypatch.setattr(bot, 'ALPACA_KONTO', None)
    bot.speicherorte_einrichten(tmp_path / 'Trading_Bot')
    bot.EREIGNISSE.clear()
    bot.KURSFEHLER.clear()
    bot.SPREADS.clear()
    return fake


def no_wait(seconds):
    pass
