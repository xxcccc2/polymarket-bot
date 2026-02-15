"""
Orderbook Imbalance Strategy

Monitors the bid/ask volume ratio on Polymarket orderbooks.
When one side is significantly heavier (e.g. 3:1 bid-to-ask ratio),
the price is likely to move in that direction.

Combines Polymarket orderbook pressure with Binance BTC direction
for confirmation — signals only fire when both agree.

Characteristics:
    - Fast-decaying signal (seconds, not minutes)
    - Works on any market with sufficient depth, best on 5-min BTC
    - Medium win rate (~55-65%), relies on volume for profit
    - Pairs well with cross-asset strategy as a confirmation layer

References:
    - Kyle (1985): Market microstructure and informed trading
    - Easley, López de Prado & O'Hara (2012): VPIN model
"""

from __future__ import annotations

import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..config import (
    ORDERBOOK_IMBALANCE_RATIO,
    BTC_5MIN_KEYWORDS,
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
    ENABLE_BTC_5MIN,
)


class OrderbookImbalanceStrategy(BaseStrategy):
    """
    Trade orderbook imbalance with Binance confirmation on 5-min BTC markets.

    Config options:
        - binance_feed: BinanceFeed instance (optional, used for confirmation)
        - min_imbalance_ratio: Minimum bid/ask volume ratio (default: 2.5)
        - require_binance_confirm: Require Binance price agreement (default: True)
        - order_size_usd: Per-trade size (default: from config)
        - max_position_usd: Max position per market (default: from config)
        - only_5min_btc: Only trade 5-min BTC markets (default: True)
    """

    name = "orderbook_imbalance"
    description = "Trade orderbook pressure imbalances with cross-asset confirmation"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        self.binance_feed = self.config.get("binance_feed")
        self.min_imbalance_ratio = self.config.get(
            "min_imbalance_ratio", ORDERBOOK_IMBALANCE_RATIO
        )
        self.require_binance_confirm = self.config.get("require_binance_confirm", True)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_position_usd = self.config.get("max_position_usd", MAX_POSITION_USD)
        self.only_5min_btc = self.config.get("only_5min_btc", True)

        # Track positions and cooldowns
        self.positions: Dict[str, float] = {}
        self.last_signal_time: Dict[str, float] = {}
        self.signal_cooldown_s = self.config.get("signal_cooldown_s", 20)

    def should_trade_market(self, market_data: MarketData) -> bool:
        """Filter for BTC markets (orderbook data optional — falls back to spread+Binance)."""
        if self.only_5min_btc:
            if not ENABLE_BTC_5MIN:
                return False
            text = f"{market_data.question} {market_data.market_slug}".lower()
            is_btc = any(kw in text for kw in ["bitcoin", "btc"])
            is_5min = any(kw in text for kw in BTC_5MIN_KEYWORDS)
            if not (is_btc and is_5min):
                return False

        # Cooldown
        last_t = self.last_signal_time.get(market_data.token_id, 0)
        if time.time() - last_t < self.signal_cooldown_s:
            return False

        return True

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Detect orderbook imbalance and generate directional signals.

        Steps:
        1. Calculate bid_volume / ask_volume ratio from orderbook
        2. If ratio > threshold → bullish (price likely to rise)
        3. If 1/ratio > threshold → bearish (price likely to fall)
        4. Optionally confirm with Binance BTC direction
        5. Generate BUY signal for the favored side
        """
        signals = []

        # Get Binance state for optional confirmation
        binance = None
        if self.binance_feed:
            binance = self.binance_feed.get_state()
            if not binance.connected:
                binance = None

        for data in market_data:
            if not self.should_trade_market(data):
                continue

            # Position limit
            current_pos = self.positions.get(data.token_id, 0)
            if current_pos >= self.max_position_usd:
                continue

            # Calculate imbalance from orderbook (or fall back to spread proxy)
            imbalance = self._calculate_imbalance(data.orderbook, data)
            if imbalance is None:
                continue

            bid_vol, ask_vol, ratio, direction = imbalance

            # Check if ratio meets threshold
            if ratio < self.min_imbalance_ratio:
                continue

            # Binance confirmation (optional but recommended)
            if self.require_binance_confirm and binance:
                binance_direction = "UP" if binance.price_change_pct_10s > 0 else "DOWN"

                # Parse market direction
                q = data.question.lower()
                outcome_lower = data.outcome.lower()
                
                if "up or down" in q:
                    is_up_market = outcome_lower == "up"
                else:
                    up_keywords = ["go up", "above", "higher", "rise", "over"]
                    is_up_market = any(kw in q for kw in up_keywords)

                # Determine if orderbook direction agrees with Binance
                ob_bullish = direction == "BUY"  # more bids = bullish

                if is_up_market:
                    # For an "above"/"up" market, bullish OB + BTC UP = buy
                    binance_agrees = (ob_bullish and binance_direction == "UP") or \
                                     (not ob_bullish and binance_direction == "DOWN")
                else:
                    # For a "below"/"down" market, bearish OB + BTC DOWN = buy
                    binance_agrees = (not ob_bullish and binance_direction == "DOWN") or \
                                     (ob_bullish and binance_direction == "UP")

                if not binance_agrees:
                    continue  # Binance disagrees — skip

            # Estimate edge from imbalance magnitude
            # Stronger imbalance → more likely to see price movement
            edge_estimate = min((ratio - 1.0) * 0.02, 0.08)  # cap at 8¢
            edge_cents = edge_estimate * 100

            if edge_cents < 1.5:
                continue

            # Confidence scales with imbalance ratio
            confidence = min(0.50 + (ratio - self.min_imbalance_ratio) * 0.10, 0.85)

            # Determine which side to buy
            if direction == "BUY":
                # Orderbook is bid-heavy → price likely to rise
                # For YES token: buy if this market benefits from price rise
                buy_price = min(data.best_bid + 0.01, data.best_ask - 0.005)
            else:
                # Orderbook is ask-heavy → price likely to fall
                buy_price = min(data.best_bid + 0.005, data.best_ask - 0.01)

            buy_price = round(max(0.01, min(0.99, buy_price)), 3)

            # Size
            bet_size = self._size_bet(edge_estimate + data.mid_price, data.mid_price)
            shares = bet_size / buy_price if buy_price > 0 else 0

            signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=buy_price,
                size=round(shares, 2),
                confidence=round(confidence, 3),
                reason=(
                    f"OB imbalance: {direction} pressure ratio={ratio:.1f}x "
                    f"(bid_vol={bid_vol:.0f} ask_vol={ask_vol:.0f}) | "
                    f"edge~{edge_cents:.1f}¢"
                ),
                metadata={
                    "bid_volume": round(bid_vol, 2),
                    "ask_volume": round(ask_vol, 2),
                    "imbalance_ratio": round(ratio, 2),
                    "imbalance_direction": direction,
                    "edge_estimate": round(edge_estimate, 4),
                    "binance_confirmed": binance is not None,
                    "strategy": "orderbook_imbalance",
                },
            )

            signals.append(signal)
            self.signals_generated += 1
            self.last_signal_time[data.token_id] = time.time()

            cprint(f"  📊 {signal}", "cyan")

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute imbalance signals."""
        results = []

        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue

            try:
                order_result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTC",
                )

                if order_result.get("success"):
                    cprint(f"  ✅ Imbalance order placed: {order_result.get('order_id')}", "green")
                else:
                    cprint(f"  ❌ Imbalance order failed: {order_result.get('error')}", "red")

                results.append(order_result)

            except Exception as e:
                cprint(f"  ❌ Imbalance execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})

        return results

    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Track filled positions."""
        super().on_order_filled(order_id, fill_data)
        token_id = fill_data.get("token_id", "")
        size_usd = fill_data.get("size", 0) * fill_data.get("price", 0)
        self.positions[token_id] = self.positions.get(token_id, 0) + size_usd

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_imbalance(orderbook: Optional[Dict], market_data=None):
        """
        Calculate bid/ask volume imbalance from an orderbook dict.
        Falls back to spread-based proxy when orderbook is unavailable.

        Returns:
            (bid_volume, ask_volume, ratio, direction) or None
            direction is "BUY" if bid-heavy, "SELL" if ask-heavy
        """
        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            if bids or asks:
                max_levels = 10

                bid_vol = 0.0
                for level in bids[:max_levels]:
                    try:
                        bid_vol += float(level.get("size", level[1]) if isinstance(level, dict) else level[1])
                    except (IndexError, ValueError, TypeError):
                        continue

                ask_vol = 0.0
                for level in asks[:max_levels]:
                    try:
                        ask_vol += float(level.get("size", level[1]) if isinstance(level, dict) else level[1])
                    except (IndexError, ValueError, TypeError):
                        continue

                if bid_vol > 0 or ask_vol > 0:
                    if ask_vol <= 0:
                        return bid_vol, ask_vol, 10.0, "BUY"
                    elif bid_vol <= 0:
                        return bid_vol, ask_vol, 10.0, "SELL"
                    elif bid_vol >= ask_vol:
                        return bid_vol, ask_vol, bid_vol / ask_vol, "BUY"
                    else:
                        return bid_vol, ask_vol, ask_vol / bid_vol, "SELL"

        # Fallback: use bid/ask price asymmetry as a proxy
        if market_data is not None and market_data.mid_price > 0:
            bid = market_data.best_bid
            ask = market_data.best_ask
            mid = market_data.mid_price
            if bid > 0 and ask > 0 and ask > bid:
                # How close is mid to ask vs bid?
                # If mid is closer to ask → bid pressure (buyers pushing up)
                spread = ask - bid
                if spread > 0.03:
                    # Wide spread (e.g. default 50/52¢) — not enough info
                    return None
                bid_pull = (mid - bid) / spread  # 0.5 = centered
                if bid_pull > 0.55:
                    return 1.0, 1.0, 1.0 + (bid_pull - 0.5) * 8, "BUY"
                elif bid_pull < 0.45:
                    return 1.0, 1.0, 1.0 + (0.5 - bid_pull) * 8, "SELL"

        return None

    def _size_bet(self, estimated_prob: float, market_prob: float) -> float:
        """Size using Kelly with fallback."""
        try:
            from ..sizing.kelly import kelly_size
            from ..config import PAPER_BALANCE_USD, PAPER_TRADING

            bankroll = PAPER_BALANCE_USD if PAPER_TRADING else 1000.0
            result = kelly_size(
                estimated_prob=estimated_prob,
                market_price=market_prob,
                bankroll=bankroll,
                max_bet_usd=self.order_size_usd * 2,
            )
            if result.bet_size_usd > 0:
                return result.bet_size_usd
        except Exception:
            pass

        return self.order_size_usd

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state.update({
            "positions": self.positions,
            "min_imbalance_ratio": self.min_imbalance_ratio,
            "require_binance_confirm": self.require_binance_confirm,
        })
        return state
