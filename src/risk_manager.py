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
from termcolor import cprint

from .config import (
    MAX_POSITION_USD,
    MAX_TOTAL_EXPOSURE_USD,
    DAILY_LOSS_LIMIT_USD,
    DAILY_PROFIT_TARGET_USD,
    MIN_BALANCE_USD,
    MIN_PRICE_CENTS,
    MAX_PRICE_CENTS,
)


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
    
    def __init__(self):
        # Positions by token_id
        self.positions: Dict[str, Position] = {}
        
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
        
        # Callbacks
        self._risk_callbacks: List[Callable[[RiskLevel, str], None]] = []
        
        # Initialize today's stats
        self._get_or_create_daily_stats()
    
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
        else:
            stats.peak_balance = max(stats.peak_balance, balance)
        
        # Check balance thresholds
        self._check_balance_risk()
    
    def _check_balance_risk(self):
        """Check if balance triggers risk alerts."""
        if self.current_balance < MIN_BALANCE_USD:
            self._set_risk_level(
                RiskLevel.CRITICAL,
                f"Balance ${self.current_balance:.2f} below minimum ${MIN_BALANCE_USD}"
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
        
        # Check daily loss limit
        stats = self._get_or_create_daily_stats()
        if stats.realized_pnl < -DAILY_LOSS_LIMIT_USD:
            self._set_risk_level(
                RiskLevel.HALTED,
                f"Daily loss limit reached: ${stats.realized_pnl:.2f}"
            )
            return False, f"Daily loss limit reached"
        
        return True, "OK"
    
    def can_open_position(
        self,
        token_id: str,
        size_usd: float,
        price: float
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Args:
            token_id: Token to trade
            size_usd: Position size in USD
            price: Entry price
            
        Returns:
            (allowed: bool, reason: str)
        """
        # Check if trading allowed
        can, reason = self.can_trade()
        if not can:
            return False, reason
        
        # Check price range
        price_cents = price * 100
        if price_cents < MIN_PRICE_CENTS or price_cents > MAX_PRICE_CENTS:
            return False, f"Price {price_cents:.0f}¢ outside safe range ({MIN_PRICE_CENTS}-{MAX_PRICE_CENTS}¢)"
        
        # Check per-market position limit
        existing = self.positions.get(token_id)
        existing_size = existing.market_value if existing else 0
        
        if existing_size + size_usd > MAX_POSITION_USD:
            return False, f"Would exceed position limit: ${existing_size + size_usd:.0f} > ${MAX_POSITION_USD}"
        
        # Check total exposure
        total_exposure = self.get_total_exposure()
        if total_exposure + size_usd > MAX_TOTAL_EXPOSURE_USD:
            return False, f"Would exceed total exposure: ${total_exposure + size_usd:.0f} > ${MAX_TOTAL_EXPOSURE_USD}"
        
        return True, "OK"
    
    def get_total_exposure(self) -> float:
        """Get total exposure across all positions."""
        return sum(p.market_value for p in self.positions.values())
    
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
            
            # Remove closed positions
            if position.size <= 0.001:
                del self.positions[token_id]
                cprint(f"📉 Position closed: {market_slug}", "yellow")
    
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
    
    def resume_trading(self):
        """Resume trading after halt."""
        if self.is_halted:
            cprint("✅ Trading resumed", "green")
            self.is_halted = False
            self.halt_reason = None
            self.risk_level = RiskLevel.NORMAL
    
    def get_status(self) -> Dict:
        """Get current risk status."""
        stats = self._get_or_create_daily_stats()
        
        return {
            "risk_level": self.risk_level.value,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "current_balance": self.current_balance,
            "total_exposure": self.get_total_exposure(),
            "exposure_pct": (
                (self.get_total_exposure() / MAX_TOTAL_EXPOSURE_USD) * 100
                if MAX_TOTAL_EXPOSURE_USD > 0 else 0
            ),
            "positions_count": len(self.positions),
            "unrealized_pnl": self.get_total_unrealized_pnl(),
            "daily_pnl": stats.realized_pnl,
            "daily_trades": stats.trades_count,
            "daily_volume": stats.volume_usd,
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
        
        cprint(f"\n  Balance: ${status['current_balance']:.2f}", "white")
        cprint(f"  Exposure: ${status['total_exposure']:.2f} ({status['exposure_pct']:.1f}%)", "white")
        cprint(f"  Positions: {status['positions_count']}", "white")
        
        pnl_color = "green" if status['unrealized_pnl'] >= 0 else "red"
        cprint(f"  Unrealized PnL: ${status['unrealized_pnl']:.2f}", pnl_color)
        
        daily_color = "green" if status['daily_pnl'] >= 0 else "red"
        cprint(f"\n  Daily PnL: ${status['daily_pnl']:.2f}", daily_color)
        cprint(f"  Daily Trades: {status['daily_trades']}", "white")
        cprint(f"  Daily Volume: ${status['daily_volume']:.2f}", "white")
        
        cprint("="*50 + "\n", "cyan")



