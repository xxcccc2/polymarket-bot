"""
Risk Manager

Enforces risk limits and circuit breakers:
- Position limits per market
- Total portfolio exposure limits
- Daily loss limits
- Balance monitoring
"""

import time
import threading
from typing import Dict, List, Optional, Callable
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum

from .logging_utils import cprint

from .config import (
    MAX_POSITION_USD,
    MAX_TOTAL_EXPOSURE_USD,
    DAILY_LOSS_LIMIT_USD,
    DAILY_PROFIT_TARGET_USD,
    MIN_BALANCE_USD,
    MIN_PRICE_CENTS,
    MAX_PRICE_CENTS,
    ORDER_SIZE_USD,
)
from .persistence import SqliteStore


# =============================================================================
# ADAPTIVE RISK CONSTANTS (bankroll-proportional)
# These override the fixed USD limits when adaptive mode is on.
# =============================================================================
# Overridable via config dict passed to RiskManager
DEFAULT_ADAPTIVE_PARAMS = {
    "enabled": True,
    "max_position_pct": 0.12,        # 12% of bankroll per market
    "max_exposure_pct": 0.30,         # 30% total exposure
    "max_single_trade_pct": 0.04,     # 4% per trade
    "daily_loss_limit_pct": 0.06,     # 6% daily loss limit
    "drawdown_throttle_pct": 0.05,    # At 5% drawdown → half size
    "drawdown_halt_pct": 0.12,        # At 12% drawdown → halt
    "min_balance_floor_pct": 0.70,    # Never let balance drop below 70% of starting
}


class RiskLevel(Enum):
    """Risk level indicators."""
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    HALTED = "halted"


@dataclass
class Position:
    """Represents a position in a market."""
    token_id: str
    market_slug: str
    side: str  # "YES" or "NO"
    size: float  # Number of shares
    avg_price: float  # Average entry price
    current_price: float = 0
    unrealized_pnl: float = 0
    realized_pnl: float = 0
    opened_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @property
    def market_value(self) -> float:
        """Current market value of position."""
        return self.size * self.current_price
    
    @property
    def cost_basis(self) -> float:
        """Total cost basis."""
        return self.size * self.avg_price
    
    def update_price(self, price: float):
        """Update current price and recalculate PnL."""
        self.current_price = price
        self.unrealized_pnl = (price - self.avg_price) * self.size
        self.updated_at = datetime.now()


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: date
    trades_count: int = 0
    volume_usd: float = 0
    realized_pnl: float = 0
    fees_paid: float = 0
    max_drawdown: float = 0
    peak_balance: float = 0
    
    @property
    def net_pnl(self) -> float:
        return self.realized_pnl - self.fees_paid


class RiskManager:
    """
    Manages trading risk with position limits and circuit breakers.
    
    Features:
    - Per-market position limits
    - Total exposure limits
    - Daily loss limits (circuit breaker)
    - Price range validation
    - Balance monitoring
    """
    
    def __init__(
        self,
        store: Optional[SqliteStore] = None,
        adaptive_config: Optional[Dict] = None,
    ):
        # Positions by token_id
        self.positions: Dict[str, Position] = {}

        # Optional persistence store
        self.store = store
        
        # Daily stats (resets each day)
        self._daily_stats: Dict[date, DailyStats] = {}
        
        # Current risk level
        self.risk_level = RiskLevel.NORMAL
        
        # Halt trading flag
        self.is_halted = False
        self.halt_reason: Optional[str] = None
        
        # Balance tracking
        self.current_balance: float = 0
        self.starting_balance: float = 0
        self.session_peak_balance: float = 0
        
        # Adaptive risk management
        _ac = dict(DEFAULT_ADAPTIVE_PARAMS)
        if adaptive_config:
            _ac.update(adaptive_config)
        self.adaptive_enabled: bool = _ac["enabled"]
        self.adaptive_max_position_pct: float = _ac["max_position_pct"]
        self.adaptive_max_exposure_pct: float = _ac["max_exposure_pct"]
        self.adaptive_max_trade_pct: float = _ac["max_single_trade_pct"]
        self.adaptive_daily_loss_pct: float = _ac["daily_loss_limit_pct"]
        self.adaptive_dd_throttle_pct: float = _ac["drawdown_throttle_pct"]
        self.adaptive_dd_halt_pct: float = _ac["drawdown_halt_pct"]
        self.adaptive_balance_floor_pct: float = _ac["min_balance_floor_pct"]
        
        # Throttle state
        self._throttle_factor: float = 1.0   # 1.0 = normal, 0.5 = half size, 0.0 = halt

        # Portfolio Greeks (updated by compute_portfolio_greeks)
        self.net_delta: float = 0.0
        self.net_gamma: float = 0.0
        
        # Callbacks
        self._risk_callbacks: List[Callable[[RiskLevel, str], None]] = []
        
        # Initialize today's stats
        self._get_or_create_daily_stats()

        # Load persisted positions if available
        if self.store:
            self._load_persisted_positions()

    def _parse_datetime(self, value: Optional[str]) -> datetime:
        if not value:
            return datetime.now()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()

    def _position_to_record(self, position: Position) -> Dict[str, Optional[float]]:
        return {
            "token_id": position.token_id,
            "market_slug": position.market_slug,
            "side": position.side,
            "size": position.size,
            "avg_price": position.avg_price,
            "current_price": position.current_price,
            "unrealized_pnl": position.unrealized_pnl,
            "realized_pnl": position.realized_pnl,
            "opened_at": position.opened_at.isoformat(),
            "updated_at": position.updated_at.isoformat(),
        }

    def _persist_position(self, position: Position) -> None:
        if not self.store:
            return
        try:
            self.store.save_position(self._position_to_record(position))
        except Exception as e:
            cprint(f"❌ Failed to persist position {position.token_id}: {e}", "red")

    def _load_persisted_positions(self) -> None:
        try:
            records = self.store.load_positions()
        except Exception as e:
            cprint(f"❌ Failed to load persisted positions: {e}", "red")
            return

        for record in records:
            try:
                position = Position(
                    token_id=record["token_id"],
                    market_slug=record.get("market_slug") or "",
                    side=record.get("side") or "YES",
                    size=float(record.get("size") or 0),
                    avg_price=float(record.get("avg_price") or 0),
                    current_price=float(record.get("current_price") or 0),
                    unrealized_pnl=float(record.get("unrealized_pnl") or 0),
                    realized_pnl=float(record.get("realized_pnl") or 0),
                    opened_at=self._parse_datetime(record.get("opened_at")),
                    updated_at=self._parse_datetime(record.get("updated_at")),
                )
            except Exception as e:
                cprint(f"❌ Failed to load persisted position: {e}", "red")
                continue

            self.positions[position.token_id] = position

        if self.positions:
            cprint(f"📦 Loaded {len(self.positions)} persisted positions", "cyan")
    
    def on_risk_change(self, callback: Callable[[RiskLevel, str], None]):
        """Register callback for risk level changes."""
        self._risk_callbacks.append(callback)
    
    def _get_or_create_daily_stats(self) -> DailyStats:
        """Get or create stats for today."""
        today = date.today()
        if today not in self._daily_stats:
            self._daily_stats[today] = DailyStats(date=today)
        return self._daily_stats[today]
    
    def set_balance(self, balance: float):
        """Update current balance."""
        self.current_balance = balance
        
        stats = self._get_or_create_daily_stats()
        if stats.peak_balance == 0:
            stats.peak_balance = balance
            self.starting_balance = balance
            self.session_peak_balance = balance
        else:
            stats.peak_balance = max(stats.peak_balance, balance)
            self.session_peak_balance = max(self.session_peak_balance, balance)
        
        # Check balance thresholds
        self._check_balance_risk()
        
        # Update adaptive throttle
        if self.adaptive_enabled:
            self._update_throttle()
    
    def _check_balance_risk(self):
        """Check if balance triggers risk alerts."""
        # Fixed floor check
        if self.current_balance < MIN_BALANCE_USD:
            self._set_risk_level(
                RiskLevel.CRITICAL,
                f"Balance ${self.current_balance:.2f} below minimum ${MIN_BALANCE_USD}"
            )
            return
        
        # Adaptive floor check (never drop below X% of starting balance)
        if self.adaptive_enabled and self.starting_balance > 0:
            floor = self.starting_balance * self.adaptive_balance_floor_pct
            if self.current_balance < floor:
                self._set_risk_level(
                    RiskLevel.HALTED,
                    f"Balance ${self.current_balance:.2f} below {self.adaptive_balance_floor_pct*100:.0f}% floor (${floor:.2f})"
                )
    
    def _set_risk_level(self, level: RiskLevel, reason: str):
        """Update risk level and notify callbacks."""
        old_level = self.risk_level
        self.risk_level = level
        
        if level == RiskLevel.HALTED:
            self.is_halted = True
            self.halt_reason = reason
            cprint(f"🛑 TRADING HALTED: {reason}", "red", attrs=["bold"])
        elif level == RiskLevel.CRITICAL:
            cprint(f"⚠️  CRITICAL RISK: {reason}", "red")
        elif level == RiskLevel.WARNING:
            cprint(f"⚠️  Risk warning: {reason}", "yellow")
        
        # Notify callbacks
        if level != old_level:
            for callback in self._risk_callbacks:
                try:
                    callback(level, reason)
                except Exception as e:
                    cprint(f"❌ Risk callback error: {e}", "red")
    
    def can_trade(self) -> tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Returns:
            (can_trade: bool, reason: str)
        """
        if self.is_halted:
            return False, f"Trading halted: {self.halt_reason}"
        
        if self.risk_level == RiskLevel.CRITICAL:
            return False, "Risk level critical"
        
        # Check daily loss limit (adaptive or fixed)
        stats = self._get_or_create_daily_stats()
        daily_limit = self._get_daily_loss_limit()
        if stats.realized_pnl < -daily_limit:
            self._set_risk_level(
                RiskLevel.HALTED,
                f"Daily loss limit reached: ${stats.realized_pnl:.2f} (limit: ${daily_limit:.2f})"
            )
            return False, f"Daily loss limit reached"
        
        # Check drawdown halt
        if self._throttle_factor <= 0:
            return False, "Drawdown halt active"
        
        return True, "OK"
    
    def can_open_position(
        self,
        token_id: str,
        size_usd: float,
        price: float,
        strategy: str = "",
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Args:
            token_id: Token to trade
            size_usd: Position size in USD
            price: Entry price
            strategy: Strategy name (used for exemptions)
            
        Returns:
            (allowed: bool, reason: str)
        """
        # Check if trading allowed
        can, reason = self.can_trade()
        if not can:
            return False, reason
        
        # Check price range (stink_bid exempt from floor — 1¢ bids are by design)
        price_cents = price * 100
        effective_min = 0.5 if strategy == "stink_bid" else MIN_PRICE_CENTS
        if price_cents < effective_min or price_cents > MAX_PRICE_CENTS:
            return False, f"Price {price_cents:.0f}¢ outside safe range ({effective_min}-{MAX_PRICE_CENTS}¢)"
        
        # Compute dynamic limits based on bankroll
        max_pos = self._get_max_position()
        max_exp = self._get_max_exposure()
        
        # Check per-market position limit
        existing = self.positions.get(token_id)
        existing_size = existing.market_value if existing else 0
        
        if existing_size + size_usd > max_pos:
            return False, f"Would exceed position limit: ${existing_size + size_usd:.0f} > ${max_pos:.0f}"
        
        # Check total exposure
        total_exposure = self.get_total_exposure()
        if total_exposure + size_usd > max_exp:
            return False, f"Would exceed total exposure: ${total_exposure + size_usd:.0f} > ${max_exp:.0f}"

        # Pre-trade shock test (additive gate — only rejects extreme risk)
        shares = size_usd / price if price > 0 else 0
        approved, worst_pnl, reason = self.pre_trade_shock_test(
            token_id=token_id,
            proposed_size=shares,
            proposed_price=price,
        )
        if not approved:
            return False, reason

        return True, "OK"
    
    def get_total_exposure(self) -> float:
        """Get total exposure across all positions."""
        return sum(p.market_value for p in self.positions.values())

    def sync_positions_from_api(self, api_positions: list) -> None:
        """
        Replace local positions with ground truth from Polymarket Data API.
        Call periodically to fix exposure when fill detection misses trades.
        """
        if not api_positions:
            return
        old_token_ids = set(self.positions.keys())
        self.positions.clear()
        for p in api_positions:
            if not isinstance(p, dict):
                continue
            size = float(p.get("size", 0) or 0)
            if size < 0.01:
                continue
            token_id = p.get("asset") or p.get("token_id")
            if not token_id:
                continue
            avg_price = float(p.get("avgPrice", 0) or 0)
            cur_price = float(p.get("curPrice", p.get("currentPrice", avg_price)) or avg_price)
            current_value = float(p.get("currentValue", 0) or 0)
            market_slug = p.get("slug") or p.get("title") or ""
            outcome = (p.get("outcome") or "Yes").upper()
            side = "YES" if outcome in ("YES", "UP") else "NO"
            self.positions[token_id] = Position(
                token_id=token_id,
                market_slug=market_slug,
                side=side,
                size=size,
                avg_price=avg_price,
                current_price=cur_price,
                unrealized_pnl=(cur_price - avg_price) * size if cur_price > 0 else 0,
            )
            if self.store:
                try:
                    self._persist_position(self.positions[token_id])
                except Exception:
                    pass
        # Remove from store any positions we no longer have
        new_token_ids = set(self.positions.keys())
        for tid in old_token_ids - new_token_ids:
            if self.store:
                try:
                    self.store.delete_position(tid)
                except Exception:
                    pass
    
    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized PnL."""
        return sum(p.unrealized_pnl for p in self.positions.values())
    
    def update_position(
        self,
        token_id: str,
        market_slug: str,
        side: str,
        size_delta: float,
        price: float,
        is_entry: bool = True
    ):
        """
        Update position after a trade.
        
        Args:
            token_id: Token traded
            market_slug: Market name
            side: "YES" or "NO"
            size_delta: Change in position size (positive for buy, negative for sell)
            price: Trade price
            is_entry: True if opening/adding, False if closing/reducing
        """
        if token_id not in self.positions:
            if size_delta <= 0:
                return  # Can't close position that doesn't exist
            
            # New position
            self.positions[token_id] = Position(
                token_id=token_id,
                market_slug=market_slug,
                side=side,
                size=size_delta,
                avg_price=price,
                current_price=price
            )
            cprint(f"📈 New position: {market_slug} {side} {size_delta:.2f} @ ${price:.3f}", "green")
            self._persist_position(self.positions[token_id])
        else:
            position = self.positions[token_id]
            
            if is_entry:
                # Adding to position - update average price
                total_cost = position.cost_basis + (size_delta * price)
                position.size += size_delta
                position.avg_price = total_cost / position.size if position.size > 0 else 0
            else:
                # Reducing position - realize PnL
                realized = (price - position.avg_price) * abs(size_delta)
                position.realized_pnl += realized
                position.size -= abs(size_delta)
                
                stats = self._get_or_create_daily_stats()
                stats.realized_pnl += realized
                
                cprint(f"💰 Realized PnL: ${realized:.2f}", "green" if realized >= 0 else "red")
            
            position.current_price = price
            position.updated_at = datetime.now()

            self._persist_position(position)
            
            # Remove closed positions
            if position.size <= 0.001:
                del self.positions[token_id]
                cprint(f"📉 Position closed: {market_slug}", "yellow")
                if self.store:
                    try:
                        self.store.delete_position(token_id)
                    except Exception as e:
                        cprint(f"❌ Failed to delete persisted position {token_id}: {e}", "red")
    
    def update_prices(self, prices: Dict[str, float]):
        """
        Update current prices for all positions.
        
        Args:
            prices: Dict of token_id -> current_price
        """
        for token_id, price in prices.items():
            if token_id in self.positions:
                self.positions[token_id].update_price(price)
    
    def record_trade(self, volume_usd: float, fee_usd: float = 0):
        """Record a trade in daily stats."""
        stats = self._get_or_create_daily_stats()
        stats.trades_count += 1
        stats.volume_usd += volume_usd
        stats.fees_paid += fee_usd
    
    # ------------------------------------------------------------------
    # Adaptive risk helpers
    # ------------------------------------------------------------------

    def _get_max_position(self) -> float:
        """Max position per market (adaptive or fixed)."""
        if self.adaptive_enabled and self.current_balance > 0:
            return self.current_balance * self.adaptive_max_position_pct * self._throttle_factor
        return MAX_POSITION_USD

    def _get_max_exposure(self) -> float:
        """Max total exposure (adaptive or fixed)."""
        if self.adaptive_enabled and self.current_balance > 0:
            return self.current_balance * self.adaptive_max_exposure_pct * self._throttle_factor
        return MAX_TOTAL_EXPOSURE_USD

    def _get_daily_loss_limit(self) -> float:
        """Daily loss limit (adaptive or fixed)."""
        if self.adaptive_enabled and self.current_balance > 0:
            return self.current_balance * self.adaptive_daily_loss_pct
        return DAILY_LOSS_LIMIT_USD

    def get_adaptive_order_size(self, base_size: float = 0) -> float:
        """Get throttle-adjusted order size.

        Strategies should call this to get the right-sized order.
        Returns a dollar amount that respects drawdown throttling
        and bankroll proportional limits.
        """
        if base_size <= 0:
            base_size = ORDER_SIZE_USD

        if self.adaptive_enabled and self.current_balance > 0:
            max_trade = self.current_balance * self.adaptive_max_trade_pct
            size = min(base_size, max_trade) * self._throttle_factor
            return round(max(size, 0.50), 2)  # floor at 50¢

        return base_size

    def _update_throttle(self) -> None:
        """Update the throttle factor based on drawdown from session peak."""
        if self.session_peak_balance <= 0:
            self._throttle_factor = 1.0
            return

        drawdown = (self.session_peak_balance - self.current_balance) / self.session_peak_balance

        if drawdown >= self.adaptive_dd_halt_pct:
            self._throttle_factor = 0.0
            self._set_risk_level(
                RiskLevel.HALTED,
                f"Drawdown {drawdown*100:.1f}% exceeded halt threshold ({self.adaptive_dd_halt_pct*100:.0f}%)"
            )
        elif drawdown >= self.adaptive_dd_throttle_pct:
            # Linear throttle between throttle threshold and halt threshold
            range_pct = self.adaptive_dd_halt_pct - self.adaptive_dd_throttle_pct
            progress = (drawdown - self.adaptive_dd_throttle_pct) / range_pct if range_pct > 0 else 1.0
            self._throttle_factor = max(0.25, 1.0 - progress * 0.75)
            if self.risk_level != RiskLevel.WARNING:
                self._set_risk_level(
                    RiskLevel.WARNING,
                    f"Drawdown {drawdown*100:.1f}% — throttling to {self._throttle_factor*100:.0f}% size"
                )
        else:
            self._throttle_factor = 1.0
            if self.risk_level in (RiskLevel.WARNING,) and not self.is_halted:
                self.risk_level = RiskLevel.NORMAL

    def get_throttle_factor(self) -> float:
        """Return current throttle factor (1.0 = normal, 0 = halted)."""
        return self._throttle_factor

    def resume_trading(self):
        """Resume trading after halt."""
        if self.is_halted:
            cprint("✅ Trading resumed", "green")
            self.is_halted = False
            self.halt_reason = None
            self.risk_level = RiskLevel.NORMAL
            self._throttle_factor = 1.0
    
    # ------------------------------------------------------------------
    # bs-p portfolio analytics (Greeks + shock testing)
    # ------------------------------------------------------------------

    def compute_portfolio_greeks(self) -> None:
        """Recompute portfolio-level delta and gamma from all open positions.

        Stores results in ``self.net_delta`` and ``self.net_gamma``.
        Lightweight enough to call every scan cycle.
        """
        if not self.positions:
            self.net_delta = 0.0
            self.net_gamma = 0.0
            return

        try:
            from .native.pmkernel import (
                NATIVE_AVAILABLE, logit_batch, greeks_batch,
                aggregate_portfolio_greeks,
            )
            import numpy as np

            if not NATIVE_AVAILABLE:
                self.net_delta = 0.0
                self.net_gamma = 0.0
                return

            pos_list = list(self.positions.values())
            n = len(pos_list)
            prices = np.array([max(0.01, min(0.99, p.current_price)) for p in pos_list], dtype=np.float64)
            sizes = np.array([p.size for p in pos_list], dtype=np.float64)

            x = logit_batch(prices)
            delta_arr, gamma_arr = greeks_batch(x)

            result = aggregate_portfolio_greeks(sizes, delta_arr, gamma_arr)
            self.net_delta = result.net_delta
            self.net_gamma = result.net_gamma

        except Exception:
            self.net_delta = 0.0
            self.net_gamma = 0.0

    def pre_trade_shock_test(
        self,
        token_id: str,
        proposed_size: float,
        proposed_price: float,
        sigma_b: float = 0.5,
        gamma: float = 1.0,
        tau: float = 0.05,
        k: float = 2.0,
    ) -> tuple[bool, float, str]:
        """Run a stress test before opening a new position.

        Simulates +-5% and +-10% probability shocks on the full portfolio
        (including the proposed trade) and checks whether worst-case PnL
        would breach 50% of the remaining daily loss budget.

        Returns:
            ``(approved, worst_case_pnl, reason)``
        """
        try:
            from .native.pmkernel import NATIVE_AVAILABLE, simulate_shock
            import numpy as np

            if not NATIVE_AVAILABLE:
                return True, 0.0, "native engine unavailable — skipping shock test"

            pos_list = list(self.positions.values())
            n = len(pos_list) + 1  # existing + proposed

            prices = np.array(
                [max(0.01, min(0.99, p.current_price)) for p in pos_list] + [proposed_price],
                dtype=np.float64,
            )
            from .native.pmkernel import logit_batch
            x_arr = logit_batch(prices)

            q_arr = np.array(
                [p.size for p in pos_list] + [proposed_size],
                dtype=np.float64,
            )
            sigma_arr = np.full(n, sigma_b, dtype=np.float64)
            gamma_arr = np.full(n, gamma, dtype=np.float64)
            tau_arr = np.full(n, tau, dtype=np.float64)
            k_arr = np.full(n, k, dtype=np.float64)

            daily_budget = self._get_daily_loss_limit()
            stats = self._get_or_create_daily_stats()
            remaining = daily_budget + stats.realized_pnl  # realized_pnl is negative when losing

            worst = 0.0
            for shock_val in [-0.10, -0.05, 0.05, 0.10]:
                shock_arr = np.full(n, shock_val, dtype=np.float64)
                result = simulate_shock(x_arr, q_arr, sigma_arr, gamma_arr, tau_arr, k_arr, shock_arr)
                total_pnl = float(result.pnl_shift.sum())
                if total_pnl < worst:
                    worst = total_pnl

            if remaining > 0 and abs(worst) > 0.5 * remaining:
                return (
                    False,
                    worst,
                    f"Shock test failed: worst PnL ${worst:.2f} exceeds 50% of remaining daily budget ${remaining:.2f}",
                )

            return True, worst, "shock test passed"

        except Exception as e:
            return True, 0.0, f"shock test error: {e}"

    def get_status(self) -> Dict:
        """Get current risk status."""
        stats = self._get_or_create_daily_stats()
        max_exp = self._get_max_exposure()
        
        return {
            "risk_level": self.risk_level.value,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "current_balance": self.current_balance,
            "starting_balance": self.starting_balance,
            "session_peak": self.session_peak_balance,
            "total_exposure": self.get_total_exposure(),
            "exposure_pct": (
                (self.get_total_exposure() / max_exp) * 100
                if max_exp > 0 else 0
            ),
            "positions_count": len(self.positions),
            "unrealized_pnl": self.get_total_unrealized_pnl(),
            "daily_pnl": stats.realized_pnl,
            "daily_trades": stats.trades_count,
            "daily_volume": stats.volume_usd,
            "throttle_factor": self._throttle_factor,
            "adaptive_enabled": self.adaptive_enabled,
            "dynamic_max_position": round(self._get_max_position(), 2),
            "dynamic_max_exposure": round(max_exp, 2),
            "dynamic_daily_loss_limit": round(self._get_daily_loss_limit(), 2),
            "dynamic_order_size": self.get_adaptive_order_size(),
            "net_delta": round(self.net_delta, 4),
            "net_gamma": round(self.net_gamma, 4),
        }
    
    def print_status(self):
        """Print formatted risk status."""
        status = self.get_status()
        
        cprint("\n" + "="*50, "cyan")
        cprint("📊 Risk Status", "cyan", attrs=["bold"])
        cprint("="*50, "cyan")
        
        level_colors = {
            "normal": "green",
            "warning": "yellow", 
            "critical": "red",
            "halted": "red"
        }
        cprint(f"  Risk Level: {status['risk_level'].upper()}", level_colors[status['risk_level']])
        
        if status['is_halted']:
            cprint(f"  ⛔ HALTED: {status['halt_reason']}", "red", attrs=["bold"])
        
        cprint(f"\n  Balance: ${status['current_balance']:.2f} (started: ${status['starting_balance']:.2f}, peak: ${status['session_peak']:.2f})", "white")
        cprint(f"  Exposure: ${status['total_exposure']:.2f} ({status['exposure_pct']:.1f}%)", "white")
        cprint(f"  Positions: {status['positions_count']}", "white")
        
        pnl_color = "green" if status['unrealized_pnl'] >= 0 else "red"
        cprint(f"  Unrealized PnL: ${status['unrealized_pnl']:.2f}", pnl_color)
        
        daily_color = "green" if status['daily_pnl'] >= 0 else "red"
        cprint(f"\n  Daily PnL: ${status['daily_pnl']:.2f}", daily_color)
        cprint(f"  Daily Trades: {status['daily_trades']}", "white")
        cprint(f"  Daily Volume: ${status['daily_volume']:.2f}", "white")
        
        if status['adaptive_enabled']:
            cprint(f"\n  🔄 Adaptive Risk (bankroll-proportional):", "cyan")
            cprint(f"    Throttle: {status['throttle_factor']*100:.0f}%", "white")
            cprint(f"    Max Position: ${status['dynamic_max_position']:.2f}", "white")
            cprint(f"    Max Exposure: ${status['dynamic_max_exposure']:.2f}", "white")
            cprint(f"    Daily Loss Limit: ${status['dynamic_daily_loss_limit']:.2f}", "white")
            cprint(f"    Order Size: ${status['dynamic_order_size']:.2f}", "white")
        
        cprint("="*50 + "\n", "cyan")



