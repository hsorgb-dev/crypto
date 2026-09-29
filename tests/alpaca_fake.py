"""Offline stand-in for alpaca-py's CryptoHistoricalDataClient and a read-only TradingClient."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_bot_v5 as bot

START_PRICES = {'BTC/USD': 65000.0, 'ETH/USD': 2600.0, 'SOL/USD': 150.0, 'XRP/USD': 0.6,
                'DOGE/USD': 0.12, 'ADA/USD': 0.4, 'AVAX/USD': 28.0, 'LINK/USD': 12.0,
                'DOT/USD': 4.5, 'LTC/USD': 70.0}


class FakeAlpacaData:
    """Answers get_crypto_bars / get_crypto_latest_quote like alpaca-py (hourly uptrend)."""

    def __init__(self, trend=1.0015, hours=300):
        self.trend, self.hours = trend, hours
        self.calls = []
        self.unlisted = set()        # symbols Alpaca does not know
        self.stale = set()           # symbols whose last quote is old
        self.quiet_hours = {}        # symbol -> hours back (1 = last complete hour) without a bar
        self.fail = None             # exception raised on every call

    def bars(self, symbol):
        now = pd.Timestamp.now(tz='UTC').floor('h')
        rows, close = [], START_PRICES[symbol]
        # The last row is the still running hour, which the bot must ignore.
        for i in range(self.hours + 1):
            opening, close = close, close * self.trend
            hours_back = self.hours - i
            if hours_back in self.quiet_hours.get(symbol, ()):
                continue
            rows.append(SimpleNamespace(
                symbol=symbol, timestamp=(now - pd.Timedelta(hours=hours_back)).to_pydatetime(),
                open=opening, high=max(opening, close) * 1.004, low=min(opening, close) * 0.996,
                close=close, volume=12.5, trade_count=40, vwap=close))
        return rows

    def last_close(self, symbol):
        return self.bars(symbol)[-2].close

    def get_crypto_bars(self, request):
        self.calls.append(('bars', request.symbol_or_symbols, request.start))
        if self.fail:
            raise self.fail
        symbol = request.symbol_or_symbols
        if symbol in self.unlisted:
            return SimpleNamespace(data={})
        start = request.start.replace(tzinfo=timezone.utc) if request.start.tzinfo is None else request.start
        return SimpleNamespace(data={symbol: [b for b in self.bars(symbol) if b.timestamp >= start]})

    def get_crypto_latest_quote(self, request):
        symbols = request.symbol_or_symbols
        self.calls.append(('quote', tuple(symbols)))
        if self.fail:
            raise self.fail
        now = datetime.now(timezone.utc)
        quotes = {}
        for symbol in symbols:
            if symbol in self.unlisted:
                continue
            mid = self.last_close(symbol) * 1.001
            quotes[symbol] = SimpleNamespace(
                symbol=symbol, bid_price=mid * 0.9995, ask_price=mid * 1.0005,
                timestamp=now - (timedelta(minutes=30) if symbol in self.stale else timedelta(seconds=3)))
        return quotes


class ReadOnlyBroker:
    """Only get_account exists; any order method would raise AttributeError."""

    def __init__(self):
        self.calls = 0

    def get_account(self):
        self.calls += 1
        return SimpleNamespace(equity='100000.00', currency='USD')


@pytest.fixture
def alpaca(tmp_path, monkeypatch):
    """A fresh V5 bot state in a temporary folder, wired to the fake data client."""
    fake = FakeAlpacaData()
    monkeypatch.setattr(bot, 'DATENQUELLE', fake)
    monkeypatch.setattr(bot, 'HANDELSPAARE', {c: i['symbol'] for c, i in bot.COINS.items()})
    monkeypatch.setattr(bot, 'ALPACA_KONTO', None)
    bot.speicherorte_einrichten(tmp_path / 'Trading_Bot')
    bot.EREIGNISSE.clear()
    bot.KURSFEHLER.clear()
    return fake


def no_wait(seconds):
    pass
