"""
Polymarket Trading Strategies

This module provides a pluggable strategy system. To add a new strategy:

1. Create a new file in this directory (e.g., my_strategy.py)
2. Inherit from BaseStrategy
3. Implement the required methods:
   - analyze() - Analyze market and return signals
   - execute() - Execute trades based on signals
4. Register your strategy in AVAILABLE_STRATEGIES below

Example:
    from .base_strategy import BaseStrategy, Signal, SignalType
    
    class MyStrategy(BaseStrategy):
        name = "my_strategy"
        description = "My custom trading strategy"
        
        def analyze(self, market_data):
            # Your analysis logic
            return [Signal(...)]
        
        def execute(self, signals):
            # Your execution logic
            pass
"""

from .base_strategy import BaseStrategy, Signal, SignalType

# Import all available strategies
from .spread_strategy import SpreadStrategy
from .arbitrage_strategy import ArbitrageStrategy
from .stink_bid_strategy import StinkBidStrategy
from .favorite_longshot_strategy import FavoriteLongshotStrategy
from .late_money_strategy import LateMoneyStrategy
from .cross_platform_arbitrage_strategy import CrossPlatformArbitrageStrategy
from .cross_asset_strategy import CrossAssetStrategy
from .terminal_convergence_strategy import TerminalConvergenceStrategy
from .orderbook_imbalance_strategy import OrderbookImbalanceStrategy
from .vpin_strategy import VPINStrategy
from .sentiment_strategy import SentimentStrategy
from .combinatorial_arb_strategy import CombinatorialArbStrategy
from .wallet_copy_strategy import WalletCopyStrategy

# Registry of available strategies
AVAILABLE_STRATEGIES = {
    "spread": SpreadStrategy,
    "arbitrage": ArbitrageStrategy,
    "stink_bid": StinkBidStrategy,
    "favorite_longshot": FavoriteLongshotStrategy,
    "late_money": LateMoneyStrategy,
    "cross_platform_arbitrage": CrossPlatformArbitrageStrategy,
    "cross_asset": CrossAssetStrategy,
    "terminal_convergence": TerminalConvergenceStrategy,
    "orderbook_imbalance": OrderbookImbalanceStrategy,
    "vpin": VPINStrategy,
    "sentiment": SentimentStrategy,
    "combinatorial_arb": CombinatorialArbStrategy,
    "wallet_copy": WalletCopyStrategy,
}


def get_strategy(name: str, **kwargs) -> BaseStrategy:
    """
    Get a strategy instance by name.
    
    Args:
        name: Strategy name (e.g., "spread", "arbitrage")
        **kwargs: Additional arguments passed to strategy constructor
        
    Returns:
        Strategy instance
        
    Raises:
        ValueError: If strategy not found
    """
    if name not in AVAILABLE_STRATEGIES:
        available = ", ".join(AVAILABLE_STRATEGIES.keys())
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    
    return AVAILABLE_STRATEGIES[name](config=kwargs if kwargs else None)


def list_strategies() -> dict:
    """List all available strategies with their descriptions."""
    return {
        name: {
            "class": cls.__name__,
            "description": cls.description
        }
        for name, cls in AVAILABLE_STRATEGIES.items()
    }



