#!/usr/bin/env python3
"""
Reverse-engineer Polymarket wallets to discover their trading strategies.

Usage:
  python scripts/analyze_wallets.py
  # or with custom wallets:
  TRACKED_WALLETS=0xabc,0xdef python scripts/analyze_wallets.py

Note: If you hit proxy errors, run with proxies disabled:
  unset http_proxy https_proxy; python scripts/analyze_wallets.py
"""

import os
import sys

# Avoid proxy blocking Data API (common in corporate envs)
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
from collections import defaultdict

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data_client import get_trades_by_user, get_positions, get_leaderboard


def _categorize_market(trade: dict) -> str:
    """Infer market category from trade metadata."""
    title = (trade.get("title") or "").lower()
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    text = f"{title} {slug}"
    if any(kw in text for kw in ["bitcoin", "btc"]):
        if any(kw in text for kw in ["up or down", "5 min", "15 min", "1 hour"]):
            return "btc_shortterm"
        return "btc"
    if any(kw in text for kw in ["ethereum", "eth", "solana", "sol", "xrp"]):
        return "crypto"
    if any(kw in text for kw in ["trump", "biden", "election", "president"]):
        return "politics"
    if any(kw in text for kw in ["sport", "nfl", "nba", "mlb", "game"]):
        return "sports"
    if any(kw in text for kw in ["fed", "rate", "inflation", "gdp"]):
        return "macro"
    return "other"


def _infer_horizon(trade: dict) -> str:
    """Infer time horizon from slug (e.g. btc-updown-15m-1234567890)."""
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    if "5 min" in slug or "5min" in slug or "-5m-" in slug:
        return "5min"
    if "15 min" in slug or "15min" in slug or "-15m-" in slug:
        return "15min"
    if "1 hour" in slug or "1h" in slug or "-1h-" in slug:
        return "1h"
    if "day" in slug or "daily" in slug:
        return "daily"
    if "week" in slug or "month" in slug or "year" in slug or "2025" in slug or "2026" in slug:
        return "longterm"
    return "unknown"


def analyze_wallet(addr: str, max_trades: int = 200) -> dict:
    """Fetch and analyze a wallet's trades and positions."""
    addr = addr.strip()
    if not addr or not addr.startswith("0x"):
        return None

    trades = []
    offset = 0
    while len(trades) < max_trades:
        batch = get_trades_by_user(addr, limit=100, offset=offset, taker_only=False)
        if not batch:
            break
        trades.extend(batch)
        if len(batch) < 100:
            break
        offset += len(batch)
    trades = trades[:max_trades]

    positions = get_positions(addr, limit=200)

    buys = [t for t in trades if (t.get("side") or "").upper() == "BUY"]
    sells = [t for t in trades if (t.get("side") or "").upper() == "SELL"]

    categories = defaultdict(int)
    horizons = defaultdict(int)
    buy_values = []
    sell_values = []

    for t in trades:
        cat = _categorize_market(t)
        categories[cat] += 1
        hor = _infer_horizon(t)
        horizons[hor] += 1
        size = float(t.get("size", 0) or 0)
        price = float(t.get("price", 0) or 0)
        val = size * price
        if (t.get("side") or "").upper() == "BUY":
            buy_values.append(val)
        else:
            sell_values.append(val)

    pos_by_cat = defaultdict(list)
    for p in positions:
        title = (p.get("title") or p.get("slug") or "").lower()
        cat = "btc_shortterm" if "up or down" in title and "bitcoin" in title else _categorize_market(p)
        pos_by_cat[cat].append(p)

    return {
        "address": addr,
        "n_trades": len(trades),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "n_positions": len(positions),
        "categories": dict(categories),
        "horizons": dict(horizons),
        "avg_buy_usd": sum(buy_values) / len(buy_values) if buy_values else 0,
        "avg_sell_usd": sum(sell_values) / len(sell_values) if sell_values else 0,
        "total_buy_usd": sum(buy_values),
        "total_sell_usd": sum(sell_values),
        "positions_by_cat": {k: len(v) for k, v in pos_by_cat.items()},
        "sample_titles": [x for x in dict.fromkeys(t.get("title") or t.get("slug") or "" for t in trades[:30]) if x],
    }


def main():
    wallets_env = os.getenv(
        "TRACKED_WALLETS",
        "0x1979ae6b7e6534de9c4539d0c205e582ca637c9d,0xd84c2b6d65dc596f49c7b6aadd6d74ca91e407b9,0x1d0034134e339a309700ff2d34e99fa2d48b0313",
    )
    wallets = [w.strip() for w in wallets_env.split(",") if w.strip()]

    print("=" * 70)
    print("Polymarket Wallet Strategy Analysis")
    print("=" * 70)

    for addr in wallets:
        print(f"\n{'─' * 70}")
        print(f"Wallet: {addr[:10]}...{addr[-6:]}")
        print("─" * 70)
        try:
            r = analyze_wallet(addr)
            if not r:
                print("  (invalid address, skipped)")
                continue
            print(f"  Trades: {r['n_trades']} total ({r['n_buys']} buys, {r['n_sells']} sells)")
            print(f"  Positions: {r['n_positions']}")
            print(f"  Avg buy size: ${r['avg_buy_usd']:.2f}  |  Avg sell: ${r['avg_sell_usd']:.2f}")
            print(f"  Categories: {r['categories']}")
            print(f"  Time horizons: {r['horizons']}")
            print(f"  Position mix: {r['positions_by_cat']}")

            # Strategy inference
            hints = []
            if r["categories"].get("btc_shortterm", 0) > 5:
                hints.append("→ BTC 5/15-min scalper (terminal_convergence style)")
            if r["categories"].get("btc", 0) > 3 and "longterm" in r["horizons"]:
                hints.append("→ BTC threshold / combo-arb style")
            if r["categories"].get("politics", 0) > 3:
                hints.append("→ Politics / event-driven")
            if r["categories"].get("crypto", 0) > 5 and r["categories"].get("btc_shortterm", 0) < 3:
                hints.append("→ Broader crypto (ETH, SOL, etc.)")
            if r["avg_buy_usd"] > 50:
                hints.append("→ Larger size (conviction)")
            if r["avg_buy_usd"] < 15 and r["n_trades"] > 20:
                hints.append("→ Small frequent trades (scalper)")
            if hints:
                print("  Inferred strategy:")
                for h in hints:
                    print(f"    {h}")

            samples = [s for s in r.get("sample_titles", []) if s][:5]
            if samples:
                print("  Sample markets:")
                for s in samples:
                    print(f"    • {s[:60]}{'...' if len(s) > 60 else ''}")

        except Exception as e:
            print(f"  Error: {e}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
