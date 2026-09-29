"""Offline stand-in for the Kraken public API: synthetic hourly candles, no network."""
import pandas as pd
import pytest
import requests

import crypto_bot_v41 as bot

PAIRS = {'bitcoin': ('XXBTZEUR', 'XBT/EUR', 60000.0),
         'ethereum': ('XETHZEUR', 'ETH/EUR', 2400.0),
         'solana': ('SOLEUR', 'SOL/EUR', 140.0)}


class FakeKraken:
    """Answers AssetPairs/Ticker/OHLC like Kraken. `trend` is the hourly price factor."""

    def __init__(self, trend=1.0015, hours=300, fail_ticker=False):
        self.trend, self.hours, self.fail_ticker = trend, hours, fail_ticker
        self.calls = []
        self.price_factor = {coin: 1.001 for coin in PAIRS}

    def candles(self, start):
        now = pd.Timestamp.now(tz='UTC').floor('h')
        rows, close = [], start
        # The last row is the still running hour, which the bot must ignore.
        for i in range(self.hours + 1):
            opening, close = close, close * self.trend
            t = now - pd.Timedelta(hours=self.hours - i)
            rows.append([int(t.timestamp()), str(opening), str(max(opening, close) * 1.004),
                         str(min(opening, close) * 0.996), str(close), str(close), '12.5', 40])
        return rows

    def last_close(self, coin):
        return float(self.candles(PAIRS[coin][2])[-2][4])

    def __call__(self, endpunkt, parameter=None):
        self.calls.append((endpunkt, dict(parameter or {})))
        if endpunkt == 'AssetPairs':
            return {kennung: {'wsname': ws} for kennung, ws, _ in PAIRS.values()}
        if endpunkt == 'Ticker':
            if self.fail_ticker:
                raise requests.exceptions.ConnectionError('Kraken nicht erreichbar')
            return {kennung: {'c': [str(self.last_close(coin) * self.price_factor[coin]), '1.0']}
                    for coin, (kennung, _, _) in PAIRS.items()}
        if endpunkt == 'OHLC':
            coin = next(c for c, (k, _, _) in PAIRS.items() if k == parameter['pair'])
            return {parameter['pair']: self.candles(PAIRS[coin][2]), 'last': 0}
        raise AssertionError(endpunkt)


@pytest.fixture
def kraken(tmp_path, monkeypatch):
    """A fresh bot state in a temporary folder, wired to the fake API."""
    fake = FakeKraken()
    monkeypatch.setattr(bot, 'kraken_anfrage', fake)
    monkeypatch.setattr(bot, 'HANDELSPAARE', {c: k for c, (k, _, _) in PAIRS.items()})
    bot.speicherorte_einrichten(tmp_path / 'Trading_Bot')
    bot.EREIGNISSE.clear()
    return fake


def no_wait(seconds):
    pass
