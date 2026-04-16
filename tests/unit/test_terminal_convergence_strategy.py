from __future__ import annotations

from datetime import datetime, timezone

from src.strategies.base_strategy import MarketData
from src.strategies.terminal_convergence_strategy import (
    TerminalConvergenceStrategy,
    _crypto_taker_fee_cents,
)


class DummyBinanceState:
    def __init__(self):
        self.last_price = 100500.0
        self.price_change_pct_10s = 0.05
        self.price_change_pct_30s = 0.08
        self.price_change_pct_60s = 0.12
        self.bid_pressure = 0.7
        self.volatility_5m = 0.3


class DummyBinanceFeed:
    connected = True

    def get_state(self, symbol="btc"):
        return DummyBinanceState()

    def get_1h_candle_open(self, asset):
        return 100000.0


def _market(**overrides) -> MarketData:
    now = datetime.now(timezone.utc)
    defaults = dict(
        token_id="token-1",
        condition_id="cond-1",
        market_slug="btc-updown-1h-demo",
        question="Bitcoin Up or Down - 1 hour",
        outcome="Up",
        best_bid=0.89,
        best_ask=0.91,
        mid_price=0.90,
        spread=0.02,
        volume_24h=100000.0,
        liquidity=50000.0,
        last_price=0.90,
        has_real_quotes=True,
        accepting_orders=True,
        fees_enabled=True,
        fee_rate_bps=75.0,
        data_source_quality="live_quotes",
        timestamp=now,
        orderbook={"bids": [[0.89, 10]], "asks": [[0.91, 10]]},
        end_date_ts=now.timestamp() + 60,
    )
    defaults.update(overrides)
    return MarketData(**defaults)


def test_terminal_requires_real_quotes_and_open_market():
    strategy = TerminalConvergenceStrategy({"binance_feed": DummyBinanceFeed(), "1h_only": True})

    assert strategy.should_trade_market(_market(has_real_quotes=False)) is False
    assert strategy.should_trade_market(_market(accepting_orders=False)) is False
    assert strategy.should_trade_market(_market(is_resolved=True)) is False


def test_terminal_fee_model_uses_market_fee_configuration():
    fee_low = _crypto_taker_fee_cents(0.9, fee_rate_bps=25, fees_enabled=True)
    fee_high = _crypto_taker_fee_cents(0.9, fee_rate_bps=100, fees_enabled=True)

    assert fee_high > fee_low > 0
    assert _crypto_taker_fee_cents(0.9, fee_rate_bps=100, fees_enabled=False) == 0


def test_terminal_execute_uses_gtd_expiration():
    strategy = TerminalConvergenceStrategy({"binance_feed": DummyBinanceFeed(), "1h_only": True})
    signal = strategy.analyze([_market()])[0]

    class DummyOrderManager:
        def __init__(self):
            self.calls = []

        def place_limit_order(self, **kwargs):
            self.calls.append(kwargs)
            return {"success": True, "order_id": "oid-1"}

    om = DummyOrderManager()
    strategy.execute([signal], om)

    assert om.calls
    assert om.calls[0]["order_type"] == "GTD"
    assert om.calls[0]["expiration"] is not None
