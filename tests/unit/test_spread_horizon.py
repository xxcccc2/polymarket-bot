import os

os.environ["SPREAD_ONLY_CRYPTO_MARKETS"] = "true"
os.environ["SPREAD_ALLOWED_HORIZONS"] = "15m"
os.environ["SPREAD_ALLOWED_ASSETS"] = "btc"

from src.strategies.base_strategy import MarketData
from src.strategies.spread_strategy import SpreadStrategy


def market(slug: str) -> MarketData:
    return MarketData("t", "c", slug, slug, "YES", .45, .55, .5, .1, 10000, 1000, .5)


def test_spread_accepts_only_configured_horizon():
    strategy = SpreadStrategy()
    assert strategy.should_trade_market(market("bitcoin-updown-15m"))
    assert not strategy.should_trade_market(market("bitcoin-updown-5m"))
    assert not strategy.should_trade_market(market("ethereum-updown-15m"))
