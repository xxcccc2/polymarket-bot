"""
Arbitrage Strategy

Risk-free profit when YES + NO prices sum to less than $1.

Concept:
- Monitor both YES and NO token prices for each market
- When combined price < $1 (e.g., YES=48¢ + NO=50¢ = 98¢)
- Buy both YES and NO tokens
- Guaranteed $1 payout on resolution = risk-free profit

Example:
- Buy YES at 48¢ + NO at 50¢ = 98¢ total cost
- Market resolves → one pays $1, other pays $0
- Guaranteed $1 return on 98¢ = 2.04% profit (risk-free)
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from ..logging_utils import cprint

from .base_strategy import (
    BaseStrategy,
    Signal,
    SignalType,
    MarketData
)
from ..config import ORDER_SIZE_USD, TRADING_FEE_RATE


class ArbitrageStrategy(BaseStrategy):
    """
    Arbitrage Strategy - Buy YES+NO when combined < $1 for risk-free profit.
    
    Configuration options:
        - min_profit_cents: Minimum profit in cents to execute (default: 1)
        - min_profit_pct: Minimum profit percentage after fees (default: 0.5%)
        - order_size_usd: USD per side of arb (default: from config)
        - max_arbs_active: Maximum concurrent arbitrage positions (default: 10)
        - include_fees: Account for trading fees in calculation (default: True)
    """
    
    name = "arbitrage"
    description = "Risk-free arbitrage when YES + NO < $1"
    version = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Strategy parameters
        self.min_profit_cents = self.config.get("min_profit_cents", 1)  # 1 cent minimum
        self.min_profit_pct = self.config.get("min_profit_pct", 0.005)  # 0.5% after fees
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_arbs_active = self.config.get("max_arbs_active", 10)
        self.include_fees = self.config.get("include_fees", True)
        
        # Track active arbitrage positions
        self.active_arbs: Dict[str, Dict] = {}  # condition_id -> arb info
        self.completed_arbs: List[Dict] = []  # History
        
        # Market pairs cache: condition_id -> {yes_token_id, no_token_id, ...}
        self.market_pairs: Dict[str, Dict] = {}
        
        # Stats
        self.opportunities_found = 0
        self.arbs_executed = 0
        self.total_profit = 0.0
    
    def register_market_pair(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        market_slug: str,
        question: str
    ):
        """
        Register a YES/NO token pair for a market.
        Must be called before strategy can find arbitrage opportunities.
        """
        self.market_pairs[condition_id] = {
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "market_slug": market_slug,
            "question": question
        }
    
    def _find_arb_opportunity(
        self,
        yes_data: MarketData,
        no_data: MarketData
    ) -> Optional[Dict]:
        """
        Check if there's an arbitrage opportunity between YES and NO.
        
        Returns:
            Opportunity dict or None if no arb exists
        """
        # Get best ask prices (what we'd pay to buy)
        yes_ask = yes_data.best_ask
        no_ask = no_data.best_ask
        
        # Total cost to buy both
        total_cost = yes_ask + no_ask
        
        # Calculate gross profit
        gross_profit = 1.0 - total_cost  # $1 payout - cost
        gross_profit_pct = gross_profit / total_cost if total_cost > 0 else 0
        
        # Account for fees (buy YES + buy NO = 2 trades)
        if self.include_fees:
            fees = total_cost * TRADING_FEE_RATE * 2  # Fee on each buy
            net_profit = gross_profit - fees
            net_profit_pct = net_profit / total_cost if total_cost > 0 else 0
        else:
            net_profit = gross_profit
            net_profit_pct = gross_profit_pct
        
        # Check if profitable
        profit_cents = net_profit * 100
        
        if profit_cents < self.min_profit_cents:
            return None
        
        if net_profit_pct < self.min_profit_pct:
            return None
        
        return {
            "condition_id": yes_data.condition_id,
            "market_slug": yes_data.market_slug,
            "yes_token_id": yes_data.token_id,
            "no_token_id": no_data.token_id,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "total_cost": total_cost,
            "gross_profit": gross_profit,
            "net_profit": net_profit,
            "net_profit_pct": net_profit_pct,
            "profit_cents": profit_cents,
        }
    
    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Find arbitrage opportunities across YES/NO pairs.
        
        Note: This requires market_data to contain both YES and NO tokens
        for the same markets. Call register_market_pair() first.
        """
        signals = []
        
        # Group market data by condition_id
        by_condition: Dict[str, List[MarketData]] = {}
        for data in market_data:
            cid = data.condition_id
            if cid not in by_condition:
                by_condition[cid] = []
            by_condition[cid].append(data)
        
        # Check each market for arb opportunities
        for condition_id, tokens in by_condition.items():
            # Need exactly 2 tokens (YES and NO)
            if len(tokens) != 2:
                continue
            
            # Skip if we already have an active arb on this market
            if condition_id in self.active_arbs:
                continue
            
            # Check total arb limit
            if len(self.active_arbs) >= self.max_arbs_active:
                break
            
            # Identify YES and NO tokens
            yes_data = None
            no_data = None
            
            for token in tokens:
                if token.outcome.upper() == "YES":
                    yes_data = token
                elif token.outcome.upper() == "NO":
                    no_data = token
            
            if not yes_data or not no_data:
                continue
            
            # Check for arbitrage opportunity
            opportunity = self._find_arb_opportunity(yes_data, no_data)
            
            if not opportunity:
                continue
            
            self.opportunities_found += 1
            
            cprint(
                f"🎯 ARBITRAGE FOUND: {yes_data.market_slug[:40]} | "
                f"YES={opportunity['yes_ask']*100:.1f}¢ + NO={opportunity['no_ask']*100:.1f}¢ = "
                f"{opportunity['total_cost']*100:.1f}¢ | "
                f"Profit: {opportunity['profit_cents']:.1f}¢ ({opportunity['net_profit_pct']*100:.2f}%)",
                "green", attrs=["bold"]
            )
            
            # Calculate position sizes
            # We want to spend order_size_usd total, split between YES and NO
            total_to_spend = self.order_size_usd
            yes_spend = total_to_spend * (opportunity['yes_ask'] / opportunity['total_cost'])
            no_spend = total_to_spend * (opportunity['no_ask'] / opportunity['total_cost'])
            
            yes_size = yes_spend / opportunity['yes_ask']
            no_size = no_spend / opportunity['no_ask']
            
            # Use minimum size to ensure equal shares
            shares = min(yes_size, no_size)
            
            # Generate BUY signals for both YES and NO
            yes_signal = Signal(
                signal_type=SignalType.BUY,
                token_id=yes_data.token_id,
                market_slug=yes_data.market_slug,
                side="YES",
                price=opportunity['yes_ask'],
                size=shares,
                confidence=0.95,  # High confidence - it's arbitrage!
                reason=f"Arbitrage: Buy YES @ {opportunity['yes_ask']*100:.1f}¢",
                metadata={
                    "strategy": "arbitrage",
                    "arb_id": condition_id,
                    "leg": "YES",
                    "total_cost": opportunity['total_cost'],
                    "expected_profit_pct": opportunity['net_profit_pct'],
                }
            )
            
            no_signal = Signal(
                signal_type=SignalType.BUY,
                token_id=no_data.token_id,
                market_slug=no_data.market_slug,
                side="NO",
                price=opportunity['no_ask'],
                size=shares,
                confidence=0.95,
                reason=f"Arbitrage: Buy NO @ {opportunity['no_ask']*100:.1f}¢",
                metadata={
                    "strategy": "arbitrage",
                    "arb_id": condition_id,
                    "leg": "NO",
                    "total_cost": opportunity['total_cost'],
                    "expected_profit_pct": opportunity['net_profit_pct'],
                }
            )
            
            signals.extend([yes_signal, no_signal])
            self.signals_generated += 2
        
        return signals
    
    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """
        Execute arbitrage by placing both YES and NO orders.
        
        Important: Both legs must fill for the arb to be complete.
        If only one fills, we have directional risk.
        """
        results = []
        
        # Group signals by arb_id (condition_id)
        arbs: Dict[str, List[Signal]] = {}
        for signal in signals:
            arb_id = signal.metadata.get("arb_id")
            if arb_id:
                if arb_id not in arbs:
                    arbs[arb_id] = []
                arbs[arb_id].append(signal)
        
        # Execute each arb (both legs together)
        for arb_id, arb_signals in arbs.items():
            if len(arb_signals) != 2:
                cprint(f"⚠️ Incomplete arb signals for {arb_id}", "yellow")
                continue
            
            yes_signal = next((s for s in arb_signals if s.metadata.get("leg") == "YES"), None)
            no_signal = next((s for s in arb_signals if s.metadata.get("leg") == "NO"), None)
            
            if not yes_signal or not no_signal:
                continue
            
            try:
                # Place YES order
                yes_result = order_manager.place_limit_order(
                    token_id=yes_signal.token_id,
                    side="BUY",
                    price=yes_signal.price,
                    size=yes_signal.size,
                    order_type="GTC",
                    market_slug=yes_signal.market_slug,
                    metadata={"strategy": "arbitrage", "leg": "YES", "arb_id": arb_id}
                )
                
                # Place NO order
                no_result = order_manager.place_limit_order(
                    token_id=no_signal.token_id,
                    side="BUY",
                    price=no_signal.price,
                    size=no_signal.size,
                    order_type="GTC",
                    market_slug=no_signal.market_slug,
                    metadata={"strategy": "arbitrage", "leg": "NO", "arb_id": arb_id}
                )
                
                if yes_result.get("success") and no_result.get("success"):
                    # Track the arb
                    self.active_arbs[arb_id] = {
                        "market_slug": yes_signal.market_slug,
                        "yes_order_id": yes_result.get("order_id"),
                        "no_order_id": no_result.get("order_id"),
                        "yes_price": yes_signal.price,
                        "no_price": no_signal.price,
                        "size": yes_signal.size,
                        "total_cost": yes_signal.price + no_signal.price,
                        "expected_profit_pct": yes_signal.metadata.get("expected_profit_pct"),
                        "yes_filled": False,
                        "no_filled": False,
                        "created_at": datetime.now()
                    }
                    
                    self.arbs_executed += 1
                    
                    cprint(
                        f"✅ Arbitrage placed: {yes_signal.market_slug[:40]} | "
                        f"YES @ {yes_signal.price*100:.1f}¢ + NO @ {no_signal.price*100:.1f}¢",
                        "green"
                    )
                else:
                    # Partial failure - need to handle
                    if yes_result.get("success") and not no_result.get("success"):
                        cprint(f"⚠️ Arb partial: YES filled, NO failed - cancelling YES", "yellow")
                        order_manager.cancel_order(yes_result.get("order_id"), "Arb leg failed")
                    elif no_result.get("success") and not yes_result.get("success"):
                        cprint(f"⚠️ Arb partial: NO filled, YES failed - cancelling NO", "yellow")
                        order_manager.cancel_order(no_result.get("order_id"), "Arb leg failed")
                
                results.extend([yes_result, no_result])
                
            except Exception as e:
                cprint(f"❌ Arb execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})
        
        return results
    
    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Track arb leg fills."""
        super().on_order_filled(order_id, fill_data)
        
        # Find which arb this order belongs to
        for arb_id, arb in self.active_arbs.items():
            if order_id == arb.get("yes_order_id"):
                arb["yes_filled"] = True
                cprint(f"✅ Arb YES leg filled: {arb['market_slug'][:40]}", "green")
                
            elif order_id == arb.get("no_order_id"):
                arb["no_filled"] = True
                cprint(f"✅ Arb NO leg filled: {arb['market_slug'][:40]}", "green")
            
            # Check if arb is complete
            if arb.get("yes_filled") and arb.get("no_filled"):
                profit = (1.0 - arb["total_cost"]) * arb["size"]
                self.total_profit += profit
                
                cprint(
                    f"\n💰 ARBITRAGE COMPLETE: {arb['market_slug'][:40]}\n"
                    f"   Total Cost: ${arb['total_cost'] * arb['size']:.2f}\n"
                    f"   Guaranteed Return: ${arb['size']:.2f}\n"
                    f"   Profit: ${profit:.2f} ({arb['expected_profit_pct']*100:.2f}%)\n",
                    "green", attrs=["bold"]
                )
                
                # Move to completed
                self.completed_arbs.append({
                    **arb,
                    "completed_at": datetime.now(),
                    "profit": profit
                })
                del self.active_arbs[arb_id]
                break
    
    def on_order_cancelled(self, order_id: str, reason: str):
        """Handle arb leg cancellation."""
        super().on_order_cancelled(order_id, reason)
        
        # If one leg cancelled, cancel the other
        for arb_id, arb in list(self.active_arbs.items()):
            if order_id == arb.get("yes_order_id") or order_id == arb.get("no_order_id"):
                cprint(f"⚠️ Arb cancelled: {arb['market_slug'][:40]} - {reason}", "yellow")
                del self.active_arbs[arb_id]
                break
    
    def get_state(self) -> Dict[str, Any]:
        """Get strategy state."""
        state = super().get_state()
        state.update({
            "active_arbs": len(self.active_arbs),
            "arbs_executed": self.arbs_executed,
            "opportunities_found": self.opportunities_found,
            "total_profit": self.total_profit,
            "completed_arbs": len(self.completed_arbs),
        })
        return state
    
    def print_status(self):
        """Print arbitrage status."""
        cprint("\n" + "="*50, "cyan")
        cprint("⚖️ Arbitrage Strategy Status", "cyan", attrs=["bold"])
        cprint("="*50, "cyan")
        
        cprint(f"  Active Arbs: {len(self.active_arbs)}/{self.max_arbs_active}", "white")
        cprint(f"  Opportunities Found: {self.opportunities_found}", "white")
        cprint(f"  Arbs Executed: {self.arbs_executed}", "white")
        cprint(f"  Completed: {len(self.completed_arbs)}", "white")
        cprint(f"  Total Profit: ${self.total_profit:.2f}", "green" if self.total_profit > 0 else "white")
        
        if self.active_arbs:
            cprint("\n  Active Arbitrages:", "cyan")
            for arb_id, arb in list(self.active_arbs.items())[:5]:
                status = "⏳"
                if arb.get("yes_filled") and arb.get("no_filled"):
                    status = "✅"
                elif arb.get("yes_filled") or arb.get("no_filled"):
                    status = "🔄"
                
                cprint(
                    f"    {status} {arb['market_slug'][:35]}... | "
                    f"Cost: {arb['total_cost']*100:.1f}¢ | "
                    f"Profit: {arb['expected_profit_pct']*100:.2f}%",
                    "white"
                )
        
        cprint("="*50 + "\n", "cyan")

