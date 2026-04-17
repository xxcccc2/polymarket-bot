"""
Cross-Asset Latency Arbitrage Strategy

THE #1 EDGE for 5-minute BTC markets.

Core thesis: BTC spot price moves on Binance in milliseconds.
Polymarket's orderbook adjusts in seconds. That lag is alpha.

When Binance BTC pumps 0.15%+ in <10s and Polymarket hasn't repriced,
buy YES on "BTC Up" (or NO on "BTC Down") before the book catches up.

References:
    - Snowberg, Wolfers & Zitzewitz (2007, 2011): Cross-asset information flow
    - Speed edge: traditional markets have HFT, prediction markets don't
"""

from __future__ import annotations

import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..config import (
    BTC_MIN_MOVE_PCT,
    BTC_REACTION_WINDOW_SECONDS,
    BTC_MIN_CONFIDENCE,
    BTC_5MIN_KEYWORDS,
    CRYPTO_MARKET_KEYWORDS,
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
    ENABLE_CRYPTO_EVENT_INFRA,
)


class CrossAssetStrategy(BaseStrategy):
    """
    Cross-asset latency arbitrage: Binance BTC spot → Polymarket 5-min markets.

    Requires a BinanceFeed instance to be injected via config["binance_feed"].

    Config options:
        - binance_feed: BinanceFeed instance (required)
        - min_move_pct: Minimum BTC move % to trigger (default: from config)
        - reaction_window_s: Seconds after move to act (default: from config)
        - min_confidence: Minimum confidence threshold (default: from config)
        - order_size_usd: Per-trade size (default: from config)
        - max_position_usd: Max position per market (default: from config)
    """

    name = "cross_asset"
    description = "Cross-asset latency arb: Binance BTC spot → Polymarket 5-min markets"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        self.binance_feed = self.config.get("binance_feed")
        self.min_move_pct = self.config.get("min_move_pct", BTC_MIN_MOVE_PCT)
        self.reaction_window_s = self.config.get("reaction_window_s", BTC_REACTION_WINDOW_SECONDS)
        self.min_confidence = self.config.get("min_confidence", BTC_MIN_CONFIDENCE)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_position_usd = self.config.get("max_position_usd", MAX_POSITION_USD)

        # Track positions and cooldowns
        self.positions: Dict[str, float] = {}
        self.last_signal_time: Dict[str, float] = {}
        # Cooldown per market (avoid double-firing on same move)
        self.signal_cooldown_s = self.config.get("signal_cooldown_s", 30)

        # Stats
        self._signals_fired = 0
        self._signals_skipped = 0

    def should_trade_market(self, market_data: MarketData) -> bool:
        """Only trade 5-min BTC markets."""
        if not ENABLE_CRYPTO_EVENT_INFRA:
            return False

        text = f"{market_data.question} {market_data.market_slug}".lower()

        # Must be a BTC market
        is_btc = any(kw in text for kw in ["bitcoin", "btc"])
        if not is_btc:
            return False

        # Must be a short-term market
        is_5min = any(kw in text for kw in BTC_5MIN_KEYWORDS)
        if not is_5min:
            return False

        # Check cooldown for this specific market
        last_t = self.last_signal_time.get(market_data.token_id, 0)
        if time.time() - last_t < self.signal_cooldown_s:
            return False

        return True

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Detect cross-asset latency opportunities.

        For each 5-min BTC market:
        1. Get Binance BTC state (price change, velocity, direction)
        2. Determine if move is significant (> min_move_pct)
        3. Determine direction: UP → buy YES on "up" markets / NO on "down" markets
        4. Check if Polymarket price hasn't caught up yet
        5. If edge exists → generate signal with Kelly-informed sizing
        """
        signals = []

        if not self.binance_feed:
            return signals

        binance = self.binance_feed.get_state()

        if not binance.connected or binance.last_price <= 0:
            return signals

        # Check both 10s and 30s price changes — use the strongest signal
        move_10s = binance.price_change_pct_10s
        move_30s = binance.price_change_pct_30s
        velocity = binance.price_velocity

        # Determine the dominant move
        abs_move = max(abs(move_10s), abs(move_30s) * 0.7)  # weight 30s less
        dominant_move = move_10s if abs(move_10s) >= abs(move_30s) * 0.7 else move_30s

        # Not enough movement → skip
        if abs_move < self.min_move_pct:
            return signals

        # Direction: positive = BTC going UP
        btc_direction = "UP" if dominant_move > 0 else "DOWN"

        cprint(
            f"  ⚡ Binance signal: BTC {btc_direction} {abs_move:.3f}% "
            f"(10s={move_10s:+.3f}%, 30s={move_30s:+.3f}%, "
            f"vel=${velocity:+.1f}/s, vol={binance.volatility_5m:.1f}σ)",
            "cyan",
        )

        n_eligible = 0
        n_parsed = 0
        n_direction_match = 0
        n_edge_ok = 0
        best_edge = -999.0

        for data in market_data:
            if not self.should_trade_market(data):
                continue
            n_eligible += 1

            # Position limit check
            current_pos = self.positions.get(data.token_id, 0)
            if current_pos >= self.max_position_usd:
                continue

            # Determine trade direction based on market question
            q = data.question.lower()
            trade_side, market_type = self._parse_market_direction(q, data.outcome)

            if trade_side is None:
                continue
            n_parsed += 1

            # Should we buy or skip based on BTC direction?
            should_buy = self._should_buy(btc_direction, market_type, trade_side)
            if not should_buy:
                continue
            n_direction_match += 1

            # Calculate edge: difference between our estimated fair value
            # and current market price
            estimated_prob = self._estimate_probability(
                btc_direction, abs_move, binance, data
            )

            # Current market price as implied probability
            market_prob = data.mid_price

            # Edge = our estimate - market price (for a BUY)
            edge = estimated_prob - market_prob
            best_edge = max(best_edge, edge)

            if edge < 0.01:  # less than 1 cent edge → not worth it
                self._signals_skipped += 1
                continue
            n_edge_ok += 1

            # Confidence based on move size and edge
            confidence = min(
                (abs_move / self.min_move_pct) * 0.5 + (edge * 2),
                0.95,
            )

            if confidence < self.min_confidence:
                self._signals_skipped += 1
                continue

            # Size the bet — use Kelly if available, else fixed
            bet_size_usd = self._size_bet(estimated_prob, market_prob, data.token_id)
            if bet_size_usd <= 0:
                continue

            # Price: buy slightly above best bid for faster fill
            buy_price = min(data.best_bid + 0.01, data.best_ask - 0.005)
            buy_price = round(max(0.01, min(0.99, buy_price)), 3)
            shares = bet_size_usd / buy_price

            signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=buy_price,
                size=round(shares, 2),
                confidence=round(confidence, 3),
                reason=(
                    f"Cross-asset: BTC {btc_direction} {abs_move:.2f}% on Binance | "
                    f"edge={edge*100:.1f}¢ | est_prob={estimated_prob:.3f} vs mkt={market_prob:.3f}"
                ),
                metadata={
                    "btc_direction": btc_direction,
                    "btc_move_pct": round(dominant_move, 4),
                    "btc_velocity": round(velocity, 2),
                    "btc_price": binance.last_price,
                    "btc_vwap": binance.vwap_5m,
                    "btc_volatility": round(binance.volatility_5m, 4),
                    "btc_bid_pressure": round(binance.bid_pressure, 3),
                    "estimated_prob": estimated_prob,
                    "market_prob": market_prob,
                    "edge": round(edge, 4),
                    "strategy": "cross_asset",
                },
            )

            signals.append(signal)

        # Keep only the top 3 signals by edge (avoid order flood)
        max_signals_per_cycle = 3
        if len(signals) > max_signals_per_cycle:
            signals.sort(key=lambda s: s.metadata.get("edge", 0), reverse=True)
            signals = signals[:max_signals_per_cycle]

        for sig in signals:
            self.signals_generated += 1
            self._signals_fired += 1
            self.last_signal_time[sig.token_id] = time.time()
            cprint(f"  🎯 {sig}", "green")

        # Diagnostic summary when BTC moved but no signals — throttled
        if not signals and n_eligible > 0:
            now = time.time()
            last_diag = getattr(self, '_last_diag_log', 0)
            if now - last_diag >= 30:  # log at most every 30s
                self._last_diag_log = now
                edge_str = f"{best_edge*100:+.1f}¢" if best_edge > -999 else "n/a"
                cprint(
                    f"  📊 cross_asset funnel: {n_eligible} eligible → "
                    f"{n_parsed} parsed → {n_direction_match} dir_match → "
                    f"{n_edge_ok} edge_ok (best={edge_str})",
                    "dark_grey",
                )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute cross-asset signals as limit orders."""
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
                    market_slug=signal.market_slug,
                    metadata={"strategy": self.name, **signal.metadata},
                )

                if order_result.get("success"):
                    order_id = order_result.get("order_id")
                    cprint(f"  ✅ Cross-asset order placed: {order_id}", "green")
                else:
                    cprint(f"  ❌ Cross-asset order failed: {order_result.get('error')}", "red")

                results.append(order_result)

            except Exception as e:
                cprint(f"  ❌ Cross-asset execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})

        return results

    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Track filled positions."""
        super().on_order_filled(order_id, fill_data)
        token_id = fill_data.get("token_id", "")
        size_usd = fill_data.get("size", 0) * fill_data.get("price", 0)
        if fill_data.get("side") == "BUY":
            self.positions[token_id] = self.positions.get(token_id, 0) + size_usd
        elif fill_data.get("side") == "SELL":
            self.positions[token_id] = max(0, self.positions.get(token_id, 0) - size_usd)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_market_direction(question: str, outcome: str):
        """
        Parse the market question to determine what direction it predicts.

        Returns:
            (trade_side, market_type) where:
            - trade_side: "YES" — always buying the token
            - market_type: "UP" or "DOWN" — what buying this token means
            Returns (None, None) if unparseable.
        """
        q = question.lower()
        outcome_lower = outcome.lower()

        # Handle "Bitcoin Up or Down" format (outcomes = "Up" / "Down")
        if "up or down" in q:
            if outcome_lower == "up":
                return "YES", "UP"
            elif outcome_lower == "down":
                return "YES", "DOWN"
            else:
                return None, None

        # Traditional format: "Will BTC go above $97,500?"
        up_keywords = ["go up", "above", "higher", "rise", "over", "up by", "increase"]
        down_keywords = ["go down", "below", "lower", "fall", "under", "drop", "decrease"]

        is_up_market = any(kw in q for kw in up_keywords)
        is_down_market = any(kw in q for kw in down_keywords)

        if is_up_market and not is_down_market:
            market_type = "UP"
        elif is_down_market and not is_up_market:
            market_type = "DOWN"
        else:
            return None, None

        trade_side = outcome  # "YES" or "NO"
        return trade_side, market_type

    @staticmethod
    def _should_buy(btc_direction: str, market_type: str, trade_side: str) -> bool:
        """
        Determine if we should buy this token given BTC direction.

        If BTC is going UP:
          - Buy YES on "UP" markets
          - Buy NO on "DOWN" markets
        If BTC is going DOWN:
          - Buy YES on "DOWN" markets
          - Buy NO on "UP" markets
        """
        if btc_direction == "UP":
            if market_type == "UP" and trade_side == "YES":
                return True
            if market_type == "DOWN" and trade_side == "NO":
                return True
        elif btc_direction == "DOWN":
            if market_type == "DOWN" and trade_side == "YES":
                return True
            if market_type == "UP" and trade_side == "NO":
                return True
        return False

    def _estimate_probability(
        self,
        btc_direction: str,
        move_pct: float,
        binance_state,
        market_data: MarketData,
    ) -> float:
        """
        Estimate the true probability that this 5-min market resolves YES,
        given the Binance signal.

        This is a heuristic model — calibrate with historical data.
        """
        # Base: current market price is the starting point
        base_prob = market_data.mid_price

        # Adjustment based on Binance move magnitude
        # Larger moves = stronger signal = bigger adjustment
        # Scale: 0.15% move → ~5% prob shift, 0.5% move → ~15% shift
        move_factor = min(move_pct / 1.0, 0.20)  # cap at 20% adjustment

        # Bid pressure adds conviction (>0.6 = buyers dominating)
        pressure_bonus = (binance_state.bid_pressure - 0.5) * 0.05

        # Velocity adds conviction
        vel_bonus = min(abs(binance_state.price_velocity) / 100, 0.03)

        total_adjustment = move_factor + pressure_bonus + vel_bonus

        # Apply direction
        q = market_data.question.lower()
        outcome_lower = market_data.outcome.lower()
        
        # Determine if this token benefits from price going UP
        if "up or down" in q:
            is_up_market = outcome_lower == "up"
        else:
            up_keywords = ["go up", "above", "higher", "rise", "over", "up by", "increase"]
            is_up_market = any(kw in q for kw in up_keywords)

        if (btc_direction == "UP" and is_up_market) or \
           (btc_direction == "DOWN" and not is_up_market):
            estimated = base_prob + total_adjustment
        else:
            estimated = base_prob - total_adjustment

        # Clamp to valid probability range
        return max(0.01, min(0.99, estimated))

    def _size_bet(self, estimated_prob: float, market_prob: float, token_id: str = "") -> float:
        """Size the bet using inventory-aware Kelly or fixed sizing."""
        try:
            from ..sizing.kelly import kelly_size
            from ..config import PAPER_BALANCE_USD, PAPER_TRADING

            bankroll = PAPER_BALANCE_USD if PAPER_TRADING else 1000.0
            pos_usd = self.positions.get(token_id, 0.0)
            inv_q = pos_usd / market_prob if market_prob > 0 else 0.0
            result = kelly_size(
                estimated_prob=estimated_prob,
                market_price=market_prob,
                bankroll=bankroll,
                max_bet_usd=self.order_size_usd * 3,
                inventory_q=inv_q,
            )
            if result.bet_size_usd > 0:
                return result.bet_size_usd
        except Exception:
            pass

        return self.order_size_usd

    def get_state(self) -> Dict[str, Any]:
        """Get strategy state."""
        state = super().get_state()
        state.update({
            "positions": self.positions,
            "signals_fired": self._signals_fired,
            "signals_skipped": self._signals_skipped,
            "binance_connected": self.binance_feed.connected if self.binance_feed else False,
        })
        return state
