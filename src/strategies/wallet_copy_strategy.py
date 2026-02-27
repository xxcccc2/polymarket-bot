"""
Wallet Copy Strategy

Track and copy trades from best-performing Polymarket traders.
Uses the Data API to fetch trades by wallet address and leaderboard rankings.

Flow:
- Maintain list of "tracked wallets" (manual or from leaderboard)
- Poll their recent trades every scan
- When a NEW trade appears → copy it (with configurable size scaling)
- Filters: min trade size, max copy delay, market type (crypto only, etc.)
"""

from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from collections import defaultdict

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..data_client import get_trades_by_user, get_leaderboard
from ..config import (
    ORDER_SIZE_USD,
    PROXY_ADDRESS,
    TRACKED_WALLETS,
    WALLET_COPY_USE_LEADERBOARD,
    WALLET_COPY_LEADERBOARD_TOP_N,
    WALLET_COPY_LEADERBOARD_CATEGORY,
    WALLET_COPY_LEADERBOARD_PERIOD,
    WALLET_COPY_SIZE_USD,
    WALLET_COPY_SIZE_MULTIPLIER,
    WALLET_COPY_MAX_DELAY_SECONDS,
    WALLET_COPY_MIN_TRADE_USD,
    WALLET_COPY_CRYPTO_ONLY,
    WALLET_COPY_COOLDOWN_SECONDS,
)


class WalletCopyStrategy(BaseStrategy):
    """
    Copy trades from top Polymarket traders.
    
    Config:
        - tracked_wallets: List of proxy wallet addresses (manual)
        - use_leaderboard: If True, auto-fetch top N traders by PnL
        - leaderboard_top_n: How many from leaderboard (default: 5)
        - leaderboard_category: OVERALL, CRYPTO, etc.
        - leaderboard_period: DAY, WEEK, MONTH, ALL
        - copy_size_usd: Fixed USD per copy (default: from ORDER_SIZE_USD)
        - copy_size_multiplier: Scale vs tracked trade (0.5 = half their size)
        - max_copy_delay_seconds: Ignore trades older than this (default: 120)
        - min_tracked_trade_usd: Don't copy trades smaller than this
        - crypto_only: Only copy trades in crypto markets
    """
    
    name = "wallet_copy"
    description = "Copy trades from top Polymarket traders (leaderboard or manual)"
    version = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Tracked wallets (proxy addresses)
        manual = self.config.get("tracked_wallets", TRACKED_WALLETS)
        if isinstance(manual, str):
            manual = [a.strip() for a in manual.split(",") if a.strip()]
        self.tracked_wallets: List[str] = list(manual)
        
        # Leaderboard discovery
        self.use_leaderboard = self.config.get("use_leaderboard", WALLET_COPY_USE_LEADERBOARD)
        self.leaderboard_top_n = self.config.get("leaderboard_top_n", WALLET_COPY_LEADERBOARD_TOP_N)
        self.leaderboard_category = self.config.get("leaderboard_category", WALLET_COPY_LEADERBOARD_CATEGORY)
        self.leaderboard_period = self.config.get("leaderboard_period", WALLET_COPY_LEADERBOARD_PERIOD)
        
        # Copy parameters
        self.copy_size_usd = self.config.get("copy_size_usd", WALLET_COPY_SIZE_USD)
        self.copy_size_multiplier = self.config.get("copy_size_multiplier", WALLET_COPY_SIZE_MULTIPLIER)
        self.max_copy_delay_seconds = self.config.get("max_copy_delay_seconds", WALLET_COPY_MAX_DELAY_SECONDS)
        self.min_tracked_trade_usd = self.config.get("min_tracked_trade_usd", WALLET_COPY_MIN_TRADE_USD)
        self.crypto_only = self.config.get("crypto_only", WALLET_COPY_CRYPTO_ONLY)
        
        # Last seen trade IDs per wallet (to detect NEW trades)
        self._last_seen_ids: Dict[str, Set[str]] = defaultdict(set)
        self._max_ids_per_wallet = 200
        
        # Cooldown per token (avoid copy-spam)
        self._token_cooldown: Dict[str, float] = {}
        self.cooldown_seconds = self.config.get("cooldown_seconds", WALLET_COPY_COOLDOWN_SECONDS)
        
        # Stats
        self.copies_executed = 0
        self.trades_skipped = 0
        self.last_leaderboard_refresh = 0.0
        self.leaderboard_refresh_interval = 300  # 5 min
    
    def _refresh_tracked_wallets(self) -> None:
        """Optionally refresh wallet list from leaderboard."""
        if not self.use_leaderboard:
            return
        
        import time
        now = time.time()
        if now - self.last_leaderboard_refresh < self.leaderboard_refresh_interval:
            return
        
        try:
            entries = get_leaderboard(
                category=self.leaderboard_category,
                time_period=self.leaderboard_period,
                order_by="PNL",
                limit=self.leaderboard_top_n,
            )
            wallets = []
            for e in entries:
                w = e.get("proxyWallet") or e.get("proxy_wallet")
                if w and w not in wallets:
                    wallets.append(w)
            if wallets:
                self.tracked_wallets = list(dict.fromkeys(self.tracked_wallets + wallets))
                self.last_leaderboard_refresh = now
                cprint(f"   [WALLET_COPY] Leaderboard: tracking {len(self.tracked_wallets)} wallets", "cyan")
        except Exception as e:
            cprint(f"   [WALLET_COPY] Leaderboard fetch failed: {e}", "yellow")
    
    def _trade_id(self, t: Dict) -> str:
        """Unique ID for a trade (for deduplication)."""
        tx = t.get("transactionHash") or t.get("transaction_hash") or ""
        ts = t.get("timestamp", 0)
        aid = t.get("asset") or t.get("asset_id") or ""
        side = t.get("side", "")
        size = t.get("size", 0)
        price = t.get("price", 0)
        return f"{tx}_{aid}_{side}_{size}_{price}_{ts}"
    
    def _is_new_trade(self, wallet: str, trade: Dict) -> bool:
        tid = self._trade_id(trade)
        seen = self._last_seen_ids[wallet]
        if tid in seen:
            return False
        seen.add(tid)
        while len(seen) > self._max_ids_per_wallet:
            seen.pop()
        return True
    
    def _is_crypto_market(self, trade: Dict) -> bool:
        """Check if trade is in a crypto-related market."""
        title = (trade.get("title") or "").lower()
        slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
        text = f"{title} {slug}"
        keywords = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "xrp"]
        return any(kw in text for kw in keywords)
    
    def _trade_value_usd(self, trade: Dict) -> float:
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        return size * price
    
    def _trade_too_old(self, trade: Dict) -> bool:
        ts = trade.get("timestamp")
        if ts is None:
            return True
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        ts = int(ts)
        # Polymarket timestamps may be in ms
        if ts > 1e12:
            ts = ts // 1000
        import time
        age = time.time() - ts
        return age > self.max_copy_delay_seconds
    
    def _check_token_cooldown(self, token_id: str) -> bool:
        import time
        last = self._token_cooldown.get(token_id, 0)
        return (time.time() - last) < self.cooldown_seconds
    
    def _set_token_cooldown(self, token_id: str) -> None:
        import time
        self._token_cooldown[token_id] = time.time()
    
    def should_trade_market(self, market_data: MarketData) -> bool:
        """Wallet copy doesn't filter by market_data in the same way - we copy any tracked trade."""
        return True
    
    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Poll tracked wallets for new trades and generate copy signals.
        Uses market_data to validate token exists in our universe.
        """
        signals: List[Signal] = []
        token_map = {md.token_id: md for md in market_data}
        
        # Refresh leaderboard wallets periodically
        self._refresh_tracked_wallets()
        
        if not self.tracked_wallets:
            return signals
        
        our_wallet = (PROXY_ADDRESS or "").lower()
        for wallet in self.tracked_wallets:
            if our_wallet and wallet and wallet.lower() == our_wallet:
                continue  # Never copy ourselves
            try:
                trades = get_trades_by_user(wallet, limit=20, taker_only=True)
            except Exception as e:
                cprint(f"   [WALLET_COPY] Failed to fetch {wallet[:10]}...: {e}", "yellow")
                continue
            
            for trade in trades:
                # Only copy BUY (simplest; can add SELL later for position mirroring)
                if (trade.get("side") or "").upper() != "BUY":
                    continue
                
                if not self._is_new_trade(wallet, trade):
                    continue
                
                if self._trade_too_old(trade):
                    self.trades_skipped += 1
                    continue
                
                trade_val = self._trade_value_usd(trade)
                if trade_val < self.min_tracked_trade_usd:
                    self.trades_skipped += 1
                    continue
                
                if self.crypto_only and not self._is_crypto_market(trade):
                    self.trades_skipped += 1
                    continue
                
                token_id = trade.get("asset") or trade.get("asset_id")
                if not token_id:
                    continue
                
                if self._check_token_cooldown(token_id):
                    continue
                
                # Optionally require token in our market universe
                if token_id not in token_map:
                    # Still allow - we might not have this market loaded
                    md = None
                    market_slug = trade.get("slug") or trade.get("eventSlug") or "unknown"
                else:
                    md = token_map[token_id]
                    market_slug = md.market_slug
                
                price = float(trade.get("price", 0))
                size = float(trade.get("size", 0))
                
                if price <= 0 or price >= 1:
                    continue
                
                # Size: fixed USD or scaled from their trade
                size_usd = self.copy_size_usd
                if self.copy_size_multiplier != 1.0:
                    their_usd = size * price
                    size_usd = their_usd * self.copy_size_multiplier
                    size_usd = max(1, min(size_usd, self.copy_size_usd * 3))  # Cap
                
                size_shares = size_usd / price if price > 0 else 0
                if size_shares < 0.1:
                    continue
                
                self._set_token_cooldown(token_id)
                
                trader = trade.get("userName") or trade.get("pseudonym") or wallet[:12]
                reason = f"Copy {trader}: BUY {size:.1f} @ {price*100:.1f}¢"
                
                signal = Signal(
                    signal_type=SignalType.BUY,
                    token_id=token_id,
                    market_slug=market_slug,
                    side="YES",
                    price=price,
                    size=size_shares,
                    confidence=0.75,
                    reason=reason,
                    metadata={
                        "strategy": "wallet_copy",
                        "trader_wallet": wallet,
                        "trader_name": trader,
                        "original_size": size,
                        "original_price": price,
                    },
                )
                signals.append(signal)
                self.signals_generated += 1
                
                cprint(
                    f"📋 WALLET_COPY: {trader} → BUY {size_shares:.1f} @ {price*100:.1f}¢ | {market_slug[:35]}...",
                    "magenta",
                    attrs=["bold"],
                )
        
        return signals
    
    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute copy trades."""
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
                    metadata={
                        "strategy": "wallet_copy",
                        "trader": signal.metadata.get("trader_name", "unknown"),
                    },
                )
                if order_result.get("success"):
                    self.copies_executed += 1
                results.append(order_result)
            except Exception as e:
                cprint(f"❌ Wallet copy execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})
        return results
    
    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state.update({
            "tracked_wallets": len(self.tracked_wallets),
            "copies_executed": self.copies_executed,
            "trades_skipped": self.trades_skipped,
        })
        return state
