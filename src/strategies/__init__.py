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

# Registry of available strategies
AVAILABLE_STRATEGIES = {
    "spread": SpreadStrategy,
    "arbitrage": ArbitrageStrategy,
    "stink_bid": StinkBidStrategy,
    "favorite_longshot": FavoriteLongshotStrategy,
    # Add new strategies here:
    # "anchoring": AnchoringStrategy,
    # "late_money": LateMoneyStrategy,
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
    
    return AVAILABLE_STRATEGIES[name](**kwargs)


def list_strategies() -> dict:
    """List all available strategies with their descriptions."""
    return {
        name: {
            "class": cls.__name__,
            "description": cls.description
        }
        for name, cls in AVAILABLE_STRATEGIES.items()
    }



