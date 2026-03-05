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

from typing import List, Dict, Any, Optional
from collections import defaultdict
import time

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..data_client import get_trades_by_user, get_leaderboard
from ..wallet_rotation import RotationManager
from ..config import (
    ORDER_SIZE_USD,
    POLYMARKET_MIN_ORDER_SIZE,
    PROXY_ADDRESS,
    TRACKED_WALLETS,
    WALLET_COPY_BLOCKED_WALLETS,
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
    WALLET_COPY_MIN_WALLET_POLL_SECONDS,
    WALLET_ROTATION_ENABLED,
    WALLET_ROTATION_REFRESH_INTERVAL_SECONDS,
    WALLET_ROTATION_INACTIVITY_THRESHOLD_HOURS,
    WALLET_ROTATION_MAX_REPLACEMENTS_PER_CYCLE,
    WALLET_ROTATION_MIN_TRACKED_WALLETS,
    WALLET_ROTATION_CANDIDATE_POOL_SIZE,
    WALLET_ROTATION_MIN_TRADES,
    WALLET_ROTATION_MIN_CRYPTO_PCT,
    WALLET_ROTATION_MIN_SHORTTERM_PCT,
    WALLET_ROTATION_MAX_DAYS_SINCE_LAST_TRADE,
    WALLET_ROTATION_MIN_TRADES_PER_DAY,
    WALLET_ROTATION_REMOVED_COOLDOWN_HOURS,
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
        
        # Blocked wallets (hedge/MM) — never track or copy
        self._blocked = {a.lower() for a in self.config.get("blocked_wallets", WALLET_COPY_BLOCKED_WALLETS)}
        if self._blocked:
            cprint(f"   [WALLET_COPY] Blocked {len(self._blocked)} wallets (hedge/MM): {', '.join(sorted(self._blocked)[:3])}{'...' if len(self._blocked) > 3 else ''}", "yellow")

        # Tracked wallets: load from DB if persisted, else from config
        manual = self.config.get("tracked_wallets", TRACKED_WALLETS)
        if isinstance(manual, str):
            manual = [a.strip() for a in manual.split(",") if a.strip()]
        config_wallets = [w for w in manual if w.lower() not in self._blocked]
        store = self.config.get("store")
        persisted_removed_at: Dict[str, float] = {}
        if store and hasattr(store, "load_wallet_copy_state"):
            loaded = store.load_wallet_copy_state()
            if loaded:
                persisted_wallets, persisted_removed_at = loaded
                if persisted_wallets:
                    self.tracked_wallets = [w for w in persisted_wallets if w.lower() not in self._blocked]
                else:
                    self.tracked_wallets = list(config_wallets)
            else:
                self.tracked_wallets = list(config_wallets)
        else:
            self.tracked_wallets = list(config_wallets)
        
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
        self.min_wallet_poll_seconds = float(
            self.config.get("min_wallet_poll_seconds", WALLET_COPY_MIN_WALLET_POLL_SECONDS)
        )
        
        # Last seen trade IDs per wallet (to detect NEW trades)
        self._last_seen_ids: Dict[str, Dict[str, None]] = defaultdict(dict)
        self._max_ids_per_wallet = 200
        self._last_wallet_poll_ts: Dict[str, float] = {}
        
        # Cooldown per token (avoid copy-spam)
        self._token_cooldown: Dict[str, float] = {}
        self.cooldown_seconds = self.config.get("cooldown_seconds", WALLET_COPY_COOLDOWN_SECONDS)
        
        # Track which leaders we copied each token from (for SELL mirroring)
        self.copied_from: Dict[str, set[str]] = {}  # token_id -> {wallets}
        self.risk_manager = self.config.get("risk_manager")
        self._fill_hook_registered = False
        
        # Stats
        self.copies_executed = 0
        self.sells_mirrored = 0
        self.trades_skipped = 0
        self.signals_per_wallet: Dict[str, int] = defaultdict(int)  # wallet -> signal count for TUI
        self.last_leaderboard_refresh = 0.0
        self.leaderboard_refresh_interval = 300  # 5 min

        # Rotation (inactivity-driven replacement)
        self.rotation_enabled = self.config.get("rotation_enabled", WALLET_ROTATION_ENABLED)
        self._rotation_manager: Optional[RotationManager] = None
        self._last_rotation_result: Optional[dict] = None
        if self.rotation_enabled:
            self._rotation_manager = RotationManager(
                blocked_wallets=list(self._blocked),
                inactivity_threshold_hours=self.config.get(
                    "rotation_inactivity_threshold_hours",
                    WALLET_ROTATION_INACTIVITY_THRESHOLD_HOURS,
                ),
                refresh_interval_seconds=self.config.get(
                    "rotation_refresh_interval_seconds",
                    WALLET_ROTATION_REFRESH_INTERVAL_SECONDS,
                ),
                max_replacements_per_cycle=self.config.get(
                    "rotation_max_replacements_per_cycle",
                    WALLET_ROTATION_MAX_REPLACEMENTS_PER_CYCLE,
                ),
                min_tracked_wallets=self.config.get(
                    "rotation_min_tracked_wallets",
                    WALLET_ROTATION_MIN_TRACKED_WALLETS,
                ),
                candidate_pool_size=self.config.get(
                    "rotation_candidate_pool_size",
                    WALLET_ROTATION_CANDIDATE_POOL_SIZE,
                ),
                min_trades=self.config.get("rotation_min_trades", WALLET_ROTATION_MIN_TRADES),
                min_crypto_pct=self.config.get("rotation_min_crypto_pct", WALLET_ROTATION_MIN_CRYPTO_PCT),
                min_shortterm_pct=self.config.get("rotation_min_shortterm_pct", WALLET_ROTATION_MIN_SHORTTERM_PCT),
                max_days_since_last_trade=self.config.get(
                    "rotation_max_days_since_last_trade",
                    WALLET_ROTATION_MAX_DAYS_SINCE_LAST_TRADE,
                ),
                min_trades_per_day=self.config.get(
                    "rotation_min_trades_per_day",
                    WALLET_ROTATION_MIN_TRADES_PER_DAY,
                ),
                leaderboard_category=self.config.get(
                    "leaderboard_category",
                    WALLET_COPY_LEADERBOARD_CATEGORY,
                ),
                removed_cooldown_hours=self.config.get(
                    "rotation_removed_cooldown_hours",
                    WALLET_ROTATION_REMOVED_COOLDOWN_HOURS,
                ),
                initial_removed_at=persisted_removed_at,
            )
    
    def _refresh_tracked_wallets(self) -> None:
        """Refresh wallet list: rotation (inactivity-driven) or leaderboard merge."""
        if self.rotation_enabled and self._rotation_manager:
            try:
                new_tracked, result = self._rotation_manager.run_rotation(
                    self.tracked_wallets,
                    on_log=lambda msg: cprint(f"   {msg}", "cyan"),
                )
                if result.replaced or result.added:
                    self.tracked_wallets = [w for w in new_tracked if w.lower() not in self._blocked]
                    self._last_rotation_result = {
                        "replaced": result.replaced,
                        "added": result.added,
                        "reason": result.reason,
                        "score_breakdown": result.score_breakdown,
                    }
                    self._persist_wallet_copy_state()
                    cprint(
                        f"   [WALLET_COPY] Rotation: -{len(result.replaced)} +{len(result.added)} "
                        f"| now tracking {len(self.tracked_wallets)} wallets",
                        "cyan",
                        attrs=["bold"],
                    )
            except Exception as e:
                cprint(f"   [WALLET_COPY] Rotation failed: {e}", "yellow")
            return

        if not self.use_leaderboard:
            return

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
                allowed = [w for w in wallets if w.lower() not in self._blocked]
                self.tracked_wallets = [w for w in list(dict.fromkeys(self.tracked_wallets + allowed)) if w.lower() not in self._blocked]
                self.last_leaderboard_refresh = now
                self._persist_wallet_copy_state()
                cprint(f"   [WALLET_COPY] Leaderboard: tracking {len(self.tracked_wallets)} wallets", "cyan")
        except Exception as e:
            cprint(f"   [WALLET_COPY] Leaderboard fetch failed: {e}", "yellow")

    def _persist_wallet_copy_state(self) -> None:
        """Save tracked wallets and rotation cooldown to DB."""
        store = self.config.get("store")
        if not store or not hasattr(store, "save_wallet_copy_state"):
            return
        removed_at = (
            self._rotation_manager.get_removed_at()
            if self._rotation_manager
            else {}
        )
        try:
            store.save_wallet_copy_state(self.tracked_wallets, removed_at)
        except Exception as e:
            cprint(f"   [WALLET_COPY] Persist failed: {e}", "yellow")
    
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
        seen[tid] = None
        while len(seen) > self._max_ids_per_wallet:
            oldest = next(iter(seen))
            seen.pop(oldest, None)
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
        try:
            ts_num = float(ts)
        except (TypeError, ValueError):
            return True
        # Polymarket timestamps may be in ms
        if ts_num > 1e12:
            ts_num = ts_num / 1000.0
        age = time.time() - ts_num
        return age > self.max_copy_delay_seconds

    def _prune_stale_copied_from(self) -> None:
        """Drop copied-from mappings when we no longer hold a position and have no pending BUY."""
        rm = self.risk_manager
        if not rm:
            return
        positions = getattr(rm, "positions", {}) or {}
        om = self.config.get("order_manager")
        for token_id in list(self.copied_from.keys()):
            pos = positions.get(token_id)
            has_position = pos and getattr(pos, "size", 0) > 0
            # Keep mapping if we have position OR have pending wallet_copy BUY for this token
            has_pending_buy = False
            if om:
                for o in om.get_orders_for_token(token_id):
                    if not o.is_active:
                        continue
                    if (getattr(o, "metadata", {}) or {}).get("strategy") == "wallet_copy" and (o.side or "").upper() == "BUY":
                        has_pending_buy = True
                        break
            if has_position or has_pending_buy:
                continue
            self.copied_from.pop(token_id, None)

    def _ensure_fill_hook(self, order_manager) -> None:
        """Register fill hook once so provenance is tracked on real fills."""
        if self._fill_hook_registered:
            return
        if hasattr(order_manager, "on_fill"):
            order_manager.on_fill(self._on_order_fill)
            self._fill_hook_registered = True

    def _on_order_fill(self, order, fill_data: Dict[str, Any]) -> None:
        """Track copied provenance from FILLED orders only."""
        del fill_data  # callback parity with order manager
        status = getattr(order, "status", None)
        status_value = getattr(status, "value", str(status)).lower()
        if status_value != "filled":
            return
        metadata = getattr(order, "metadata", {}) or {}
        if metadata.get("strategy") != "wallet_copy":
            return
        token_id = getattr(order, "token_id", None)
        wallet = metadata.get("trader_wallet")
        side = (getattr(order, "side", "") or "").upper()
        if not token_id:
            return
        if side == "BUY":
            if wallet:
                self.copied_from.setdefault(token_id, set()).add(wallet)
            self.copies_executed += 1
        elif side == "SELL":
            if wallet:
                wallets = self.copied_from.get(token_id, set())
                wallets.discard(wallet)
                if not wallets:
                    self.copied_from.pop(token_id, None)
            else:
                self.copied_from.pop(token_id, None)
            self.sells_mirrored += 1
    
    def _check_token_cooldown(self, token_id: str) -> bool:
        last = self._token_cooldown.get(token_id, 0)
        return (time.time() - last) < self.cooldown_seconds
    
    def _set_token_cooldown(self, token_id: str) -> None:
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
        self._prune_stale_copied_from()
        
        # Refresh leaderboard wallets periodically
        self._refresh_tracked_wallets()
        
        if not self.tracked_wallets:
            return signals
        
        our_wallet = (PROXY_ADDRESS or "").lower()
        for wallet in self.tracked_wallets:
            if our_wallet and wallet and wallet.lower() == our_wallet:
                continue  # Never copy ourselves
            last_poll = self._last_wallet_poll_ts.get(wallet, 0.0)
            if (time.time() - last_poll) < self.min_wallet_poll_seconds:
                continue
            self._last_wallet_poll_ts[wallet] = time.time()
            try:
                # taker_only=False so we see their maker sells too (resting limit sells that get hit)
                trades = get_trades_by_user(wallet, limit=20, taker_only=False)
            except Exception as e:
                cprint(f"   [WALLET_COPY] Failed to fetch {wallet[:10]}...: {e}", "yellow")
                continue
            
            for trade in trades:
                side_upper = (trade.get("side") or "").upper()
                
                # SELL mirroring: if leader sells and we copied this from them, mirror the exit
                if side_upper == "SELL":
                    token_id = trade.get("asset") or trade.get("asset_id")
                    if not token_id:
                        continue
                    copied_wallets = self.copied_from.get(token_id, set())
                    if wallet not in copied_wallets:
                        continue
                    rm = self.risk_manager
                    if not rm or token_id not in rm.positions:
                        copied_wallets.discard(wallet)
                        if not copied_wallets:
                            self.copied_from.pop(token_id, None)
                        continue
                    pos = rm.positions[token_id]
                    if pos.size <= 0:
                        copied_wallets.discard(wallet)
                        if not copied_wallets:
                            self.copied_from.pop(token_id, None)
                        continue
                    if pos.size < POLYMARKET_MIN_ORDER_SIZE:
                        continue  # Skip mirror SELL: position too small for Polymarket minimum
                    if not self._is_new_trade(wallet, trade):
                        continue
                    price = float(trade.get("price", 0))
                    if price <= 0 or price >= 1:
                        continue
                    market_slug = trade.get("slug") or trade.get("eventSlug") or pos.market_slug
                    trader = trade.get("userName") or trade.get("pseudonym") or wallet[:12]
                    sell_signal = Signal(
                        signal_type=SignalType.SELL,
                        token_id=token_id,
                        market_slug=market_slug,
                        side=pos.side,
                        price=price,
                        size=pos.size,
                        confidence=0.75,
                        reason=f"Mirror SELL {trader}: exit {pos.size:.1f} @ {price*100:.1f}¢",
                        metadata={
                            "strategy": "wallet_copy",
                            "trader_wallet": wallet,
                            "trader_name": trader,
                            "entry_price": getattr(pos, "avg_price", None),
                        },
                    )
                    signals.append(sell_signal)
                    self.signals_generated += 1
                    self.signals_per_wallet[wallet] += 1
                    wallet_short = f"{wallet[:8]}...{wallet[-6:]}" if len(wallet) >= 18 else wallet
                    cprint(
                        f"📋 WALLET_COPY: {wallet_short} → SELL (mirror) {pos.size:.1f} @ {price*100:.1f}¢ | {market_slug[:35]}...",
                        "magenta",
                        attrs=["bold"],
                    )
                    continue
                
                # BUY copy
                if side_upper != "BUY":
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
                size_shares = max(POLYMARKET_MIN_ORDER_SIZE, size_shares)
                if size_shares < 0.1:
                    continue
                
                self._set_token_cooldown(token_id)
                
                trader = trade.get("userName") or trade.get("pseudonym") or wallet[:12]
                reason = f"Copy {trader}: BUY {size:.1f} @ {price*100:.1f}¢"
                # Record provenance now so SELL mirroring works even if our fill detection lags
                self.copied_from.setdefault(token_id, set()).add(wallet)
                
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
                self.signals_per_wallet[wallet] += 1

                wallet_short = f"{wallet[:8]}...{wallet[-6:]}" if len(wallet) >= 18 else wallet
                cprint(
                    f"📋 WALLET_COPY: {wallet_short} → BUY {size_shares:.1f} @ {price*100:.1f}¢ | {market_slug[:35]}...",
                    "magenta",
                    attrs=["bold"],
                )
        
        return signals
    
    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute copy trades (BUY) and mirror exits (SELL)."""
        self._ensure_fill_hook(order_manager)
        results = []
        for signal in signals:
            try:
                if signal.signal_type == SignalType.BUY:
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
                            "trader_wallet": signal.metadata.get("trader_wallet"),
                        },
                    )
                elif signal.signal_type == SignalType.SELL:
                    order_result = order_manager.place_limit_order(
                        token_id=signal.token_id,
                        side="SELL",
                        price=signal.price,
                        size=signal.size,
                        order_type="GTC",
                        market_slug=signal.market_slug,
                        metadata={
                            "strategy": "wallet_copy",
                            "trader": signal.metadata.get("trader_name", "unknown"),
                            "trader_wallet": signal.metadata.get("trader_wallet"),
                            "entry_price": signal.metadata.get("entry_price"),
                        },
                    )
                else:
                    results.append({"success": False, "error": f"Unsupported signal type: {signal.signal_type}"})
                    continue
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
            "sells_mirrored": self.sells_mirrored,
            "trades_skipped": self.trades_skipped,
            "positions_tracked": len(self.copied_from),
        })
        if self._last_rotation_result:
            state["last_rotation"] = self._last_rotation_result

        # TUI status: signals per wallet + rotation blurb when applicable
        status_parts = []
        _max_wallets_tui = 12  # Room for >5 wallets in TUI Status column
        if self.signals_per_wallet:
            # Show per-wallet signal counts: "8E9cD5: 5 | abc123: 7"
            parts = []
            for w in self.tracked_wallets[:_max_wallets_tui]:
                cnt = self.signals_per_wallet.get(w, 0)
                short = f"{w[2:8]}" if w.startswith("0x") and len(w) >= 10 else w[:6]
                parts.append(f"{short}: {cnt}")
            status_parts.append(" | ".join(parts))
        elif self.tracked_wallets:
            short = [f"{w[:6]}...{w[-4:]}" for w in self.tracked_wallets[:_max_wallets_tui]]
            status_parts.append(f"{len(self.tracked_wallets)}: {' '.join(short)}")
        else:
            status_parts.append("no wallets")
        if self._last_rotation_result and (self._last_rotation_result.get("replaced") or self._last_rotation_result.get("added")):
            r, a = len(self._last_rotation_result.get("replaced", [])), len(self._last_rotation_result.get("added", []))
            status_parts.append(f"| rot -{r}+{a}")
        state["status"] = " ".join(status_parts)
        return state
