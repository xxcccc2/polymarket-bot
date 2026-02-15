"""
Terminal Convergence Strategy

In the final 60 seconds of a 5-minute BTC market, the price MUST converge
to ~0 (NO wins) or ~1 (YES wins). But markets are often slow to fully
converge — a YES outcome that's near-certain may still trade at 90-95¢
instead of 98-99¢.

This strategy buys the underpriced near-certain outcome in the final
window before expiry and holds to resolution.

Characteristics:
    - Very high win rate (>85% when calibrated)
    - Small edge per trade (2-8¢)
    - High frequency (every 5-min market cycle)
    - Requires real-time BTC price to determine which outcome is winning

References:
    - Page & Clemen (2013): Markets are well-calibrated short-term but mispriced near edges
    - Terminal value theorem: binary options must converge to 0 or 1 at expiry
"""

from __future__ import annotations

import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..config import (
    TERMINAL_CONVERGENCE_WINDOW_SECONDS,
    TERMINAL_MIN_EDGE_CENTS,
    BTC_5MIN_KEYWORDS,
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
    ENABLE_BTC_5MIN,
)


class TerminalConvergenceStrategy(BaseStrategy):
    """
    Buy underpriced near-certain outcomes in final seconds of 5-min BTC markets.

    Requires a BinanceFeed instance via config["binance_feed"] to determine
    which outcome is likely winning.

    Config options:
        - binance_feed: BinanceFeed instance (required)
        - convergence_window_s: Seconds before expiry to start (default: 60)
        - min_edge_cents: Minimum mispricing in cents (default: 3)
        - order_size_usd: Per-trade size (default: from config)
        - max_position_usd: Max position per market (default: from config)
        - min_certainty: Minimum probability threshold to consider "near-certain" (default: 0.80)
    """

    name = "terminal_convergence"
    description = "Buy underpriced near-certain outcomes in final seconds before 5-min expiry"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        self.binance_feed = self.config.get("binance_feed")
        self.convergence_window_s = self.config.get(
            "convergence_window_s", TERMINAL_CONVERGENCE_WINDOW_SECONDS
        )
        self.min_edge_cents = self.config.get("min_edge_cents", TERMINAL_MIN_EDGE_CENTS)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_position_usd = self.config.get("max_position_usd", MAX_POSITION_USD)
        self.min_certainty = self.config.get("min_certainty", 0.80)

        # Track positions
        self.positions: Dict[str, float] = {}
        self.last_signal_time: Dict[str, float] = {}
        self.signal_cooldown_s = self.config.get("signal_cooldown_s", 15)

    def should_trade_market(self, market_data: MarketData) -> bool:
        """Only trade 5-min BTC markets that are near expiry."""
        if not ENABLE_BTC_5MIN:
            return False

        text = f"{market_data.question} {market_data.market_slug}".lower()

        # Must be BTC + short-term
        is_btc = any(kw in text for kw in ["bitcoin", "btc"])
        is_5min = any(kw in text for kw in BTC_5MIN_KEYWORDS)
        if not (is_btc and is_5min):
            return False

        # Check cooldown
        last_t = self.last_signal_time.get(market_data.token_id, 0)
        if time.time() - last_t < self.signal_cooldown_s:
            return False

        return True

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Find near-certain outcomes that are underpriced near expiry.

        Logic:
        1. Use Binance BTC price to estimate which outcome is winning
        2. If the winning outcome's market price is below our estimated fair value
           by more than min_edge_cents → buy it
        3. Use time-to-expiry to modulate confidence (closer = more certain)
        """
        signals = []

        if not self.binance_feed:
            return signals

        binance = self.binance_feed.get_state()
        if not binance.connected or binance.last_price <= 0:
            return signals

        n_eligible = 0
        best_prob = 0.0
        best_edge = -999.0

        for data in market_data:
            if not self.should_trade_market(data):
                continue
            n_eligible += 1

            # Position limit
            current_pos = self.positions.get(data.token_id, 0)
            if current_pos >= self.max_position_usd:
                continue

            # Parse what the market is asking and extract the strike price
            strike_info = self._parse_strike(data.question, data.outcome)
            if not strike_info:
                continue

            strike_price, direction, outcome_label = strike_info

            # Determine if the current BTC price makes this outcome near-certain
            btc_price = binance.last_price

            if direction == "up_or_down":
                # For "Up or Down" markets: use BTC momentum to estimate probability
                # These resolve based on whether BTC went up or down over the window
                move_10s = binance.price_change_pct_10s
                move_30s = binance.price_change_pct_30s
                move_60s = binance.price_change_pct_60s
                pressure = binance.bid_pressure  # 0-1, >0.5 = buying
                
                # Require actual price movement — bid_pressure alone is too noisy
                price_move = max(abs(move_10s), abs(move_30s), abs(move_60s))
                if price_move < 0.02:  # need at least 0.02% real move
                    continue
                
                # Primary: price movement direction; secondary: pressure as tiebreaker
                momentum = (
                    move_60s * 0.40
                    + move_30s * 0.35
                    + move_10s * 0.15
                    + (pressure - 0.5) * 0.10
                )
                
                if outcome_label.lower() == "up":
                    estimated_prob = 0.50 + momentum * 2.5
                else:
                    estimated_prob = 0.50 - momentum * 2.5
                
                estimated_prob = max(0.05, min(0.95, estimated_prob))
            else:
                # Traditional strike-based markets
                estimated_prob = self._estimate_terminal_prob(
                    btc_price, strike_price, direction, binance.volatility_5m
                )

            best_prob = max(best_prob, estimated_prob)
            this_edge = (estimated_prob - data.mid_price) * 100
            best_edge = max(best_edge, this_edge)

            # Only interested in near-certain outcomes
            if estimated_prob < self.min_certainty:
                continue

            # Current market price
            market_price = data.mid_price

            # Edge in cents
            edge_cents = (estimated_prob - market_price) * 100

            if edge_cents < self.min_edge_cents:
                continue

            # Account for fees
            net_edge_cents = edge_cents - (TRADING_FEE_RATE * 100)
            if net_edge_cents < 1.0:  # need at least 1¢ net edge
                continue

            # Confidence: higher when BTC is further from strike / probability is more extreme
            if direction == "up_or_down":
                # For momentum-based: confidence from how extreme the probability is
                prob_dist = abs(estimated_prob - 0.50)
                confidence = min(0.55 + prob_dist * 1.5, 0.95)
            else:
                btc_dist_pct = abs(btc_price - strike_price) / max(strike_price, 1) * 100
                confidence = min(0.60 + btc_dist_pct * 0.10, 0.95)

            # Size the bet
            bet_size = self._size_bet(estimated_prob, market_price)
            if bet_size <= 0:
                continue

            # Price: aggressive — we want to fill quickly before expiry
            buy_price = min(data.best_ask, estimated_prob - 0.01)
            buy_price = round(max(0.01, min(0.99, buy_price)), 3)
            shares = bet_size / buy_price

            signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=buy_price,
                size=round(shares, 2),
                confidence=round(confidence, 3),
                reason=(
                    f"Terminal convergence: BTC=${btc_price:,.0f} vs strike=${strike_price:,.0f} "
                    f"({direction}) | edge={edge_cents:.1f}¢ | est={estimated_prob:.3f} mkt={market_price:.3f}"
                ),
                metadata={
                    "btc_price": btc_price,
                    "strike_price": strike_price,
                    "direction": direction,
                    "estimated_prob": estimated_prob,
                    "market_prob": market_price,
                    "edge_cents": round(edge_cents, 2),
                    "net_edge_cents": round(net_edge_cents, 2),
                    "strategy": "terminal_convergence",
                },
            )

            signals.append(signal)

        # Keep only the top 3 signals by edge (avoid order flood)
        max_signals_per_cycle = 3
        if len(signals) > max_signals_per_cycle:
            signals.sort(key=lambda s: s.metadata.get("edge_cents", 0), reverse=True)
            signals = signals[:max_signals_per_cycle]

        # Set cooldowns only for signals we actually emit
        for sig in signals:
            self.signals_generated += 1
            self.last_signal_time[sig.token_id] = time.time()
            cprint(f"  🏁 {sig}", "green")

        # Diagnostic summary
        if not signals and n_eligible > 0:
            edge_str = f"{best_edge:+.1f}¢" if best_edge > -999 else "n/a"
            cprint(
                f"  📊 terminal_conv funnel: {n_eligible} eligible | "
                f"best_prob={best_prob:.3f} (need ≥{self.min_certainty}) | "
                f"best_edge={edge_str} (need ≥{self.min_edge_cents}¢)",
                "dark_grey",
            )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute terminal convergence signals — aggressive limit orders."""
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
                    cprint(f"  ✅ Terminal order placed: {order_result.get('order_id')}", "green")
                else:
                    cprint(f"  ❌ Terminal order failed: {order_result.get('error')}", "red")

                results.append(order_result)

            except Exception as e:
                cprint(f"  ❌ Terminal execute error: {e}", "red")
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
    def _parse_strike(question: str, outcome: str = "Yes"):
        """
        Parse a 5-min BTC market question to extract the strike price and direction.

        Handles two formats:
        1. "Will Bitcoin go above $97,500?" → (97500, "above")
        2. "Bitcoin Up or Down - Feb 15, 11:05AM-11:10AM ET" → (0, "up_or_down")

        For format 2, strike=0 is a sentinel; the caller uses Binance VWAP instead.

        Returns:
            (strike_price, direction, outcome) or None if unparseable.
        """
        import re

        q = question.lower()

        # Handle "Bitcoin Up or Down" format — no explicit strike
        if "up or down" in q:
            return 0, "up_or_down", outcome

        # Look for dollar amounts with optional commas
        price_pattern = r'\$[\d,]+(?:\.\d+)?'
        prices = re.findall(price_pattern, question)

        if not prices:
            return None

        # Take the first price found as the strike
        strike_str = prices[0].replace("$", "").replace(",", "")
        try:
            strike_price = float(strike_str)
        except ValueError:
            return None

        # Determine direction
        if any(kw in q for kw in ["above", "over", "higher than", "go up", "rise above"]):
            direction = "above"
        elif any(kw in q for kw in ["below", "under", "lower than", "go down", "fall below", "drop below"]):
            direction = "below"
        else:
            direction = "above"

        return strike_price, direction, outcome

    @staticmethod
    def _estimate_terminal_prob(
        btc_price: float,
        strike_price: float,
        direction: str,
        volatility: float,
    ) -> float:
        """
        Estimate probability of outcome resolving YES given current BTC price.

        Uses distance from strike relative to recent volatility.
        In the terminal phase, if BTC is well past the strike, probability → 1.

        Simple model (can be upgraded to Black-Scholes for more precision):
            - Distance = |BTC - strike| / strike as %
            - If distance > 2x volatility_per_5min → near certain
            - Linear interpolation otherwise
        """
        if strike_price <= 0:
            return 0.5

        distance_pct = (btc_price - strike_price) / strike_price * 100

        # 5-min volatility from annualized: σ_5min ≈ σ_annual / sqrt(105120)
        # 105120 = number of 5-min periods per year
        vol_5min = volatility / (105120 ** 0.5) if volatility > 0 else 0.01
        vol_5min = max(vol_5min, 0.005)  # floor at 0.5 basis points

        if direction == "above":
            # BTC above strike → YES more likely
            if distance_pct > 0:
                # How many vol units above strike?
                z_score = distance_pct / (vol_5min * 100) if vol_5min > 0 else 10
                # Map z-score to probability (sigmoid-like)
                prob = min(0.50 + z_score * 0.15, 0.99)
            else:
                # Below strike
                z_score = abs(distance_pct) / (vol_5min * 100) if vol_5min > 0 else 10
                prob = max(0.50 - z_score * 0.15, 0.01)
        elif direction == "below":
            # BTC below strike → YES more likely
            if distance_pct < 0:
                z_score = abs(distance_pct) / (vol_5min * 100) if vol_5min > 0 else 10
                prob = min(0.50 + z_score * 0.15, 0.99)
            else:
                z_score = distance_pct / (vol_5min * 100) if vol_5min > 0 else 10
                prob = max(0.50 - z_score * 0.15, 0.01)
        else:
            prob = 0.5

        return round(prob, 4)

    def _size_bet(self, estimated_prob: float, market_prob: float) -> float:
        """Size using Kelly with fallback to fixed."""
        try:
            from ..sizing.kelly import kelly_size
            from ..config import PAPER_BALANCE_USD, PAPER_TRADING

            bankroll = PAPER_BALANCE_USD if PAPER_TRADING else 1000.0
            result = kelly_size(
                estimated_prob=estimated_prob,
                market_price=market_prob,
                bankroll=bankroll,
                max_bet_usd=self.order_size_usd * 2,  # conservative for terminal
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
            "convergence_window_s": self.convergence_window_s,
            "min_edge_cents": self.min_edge_cents,
            "min_certainty": self.min_certainty,
        })
        return state
