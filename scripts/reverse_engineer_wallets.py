#!/usr/bin/env python3
"""
Reverse-engineer top Polymarket wallets to extract their strategy playbook.

Deep analysis: price distribution, size vs conviction, outcome preference,
timing, market selection. Outputs inferred rules you could implement.

Usage:
  TRACKED_WALLETS=0xabc,0xdef python scripts/reverse_engineer_wallets.py
  # or default best two:
  python scripts/reverse_engineer_wallets.py

Note: unset http_proxy https_proxy if Data API fails.
"""

import os
import sys
import re
from collections import defaultdict
from datetime import datetime

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.data_client import get_trades_by_user, get_positions, get_leaderboard


def _fetch_all_trades(addr: str, max_trades: int = 500) -> list:
    """Fetch trades with pagination."""
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
    return trades[:max_trades]


def _parse_ts(ts) -> float:
    """Normalize timestamp to unix seconds."""
    if ts is None:
        return 0
    try:
        t = float(ts)
        if t > 1e12:
            t /= 1000
        return t
    except (TypeError, ValueError):
        return 0


def _infer_outcome(trade: dict) -> str:
    """Infer UP/DOWN/YES/NO from trade."""
    outcome = (trade.get("outcome") or "").lower()
    title = (trade.get("title") or trade.get("slug") or "").lower()
    if "up" in outcome or "up" in title[:40]:
        return "UP"
    if "down" in outcome or "down" in title[:40]:
        return "DOWN"
    if "yes" in outcome:
        return "YES"
    if "no" in outcome:
        return "NO"
    return "?"


def _infer_horizon(trade: dict) -> str:
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    if "5 min" in slug or "5min" in slug or "-5m-" in slug:
        return "5min"
    if "15 min" in slug or "15min" in slug or "-15m-" in slug:
        return "15min"
    if "1 hour" in slug or "1h" in slug or "-1h-":
        return "1h"
    if "up or down" in slug and "bitcoin" in slug:
        return "btc_updown"
    return "other"


def _price_bucket(price: float) -> str:
    pct = price * 100
    if pct < 5:
        return "0-5¢"
    if pct < 20:
        return "5-20¢"
    if pct < 50:
        return "20-50¢"
    if pct < 80:
        return "50-80¢"
    return "80-100¢"


def _size_bucket(usd: float) -> str:
    if usd < 2:
        return "<$2"
    if usd < 5:
        return "$2-5"
    if usd < 15:
        return "$5-15"
    if usd < 50:
        return "$15-50"
    return "$50+"


def reverse_engineer(addr: str) -> dict:
    """Deep analysis of a wallet's trading behavior."""
    trades = _fetch_all_trades(addr)
    buys = [t for t in trades if (t.get("side") or "").upper() == "BUY"]
    sells = [t for t in trades if (t.get("side") or "").upper() == "SELL"]

    price_dist = defaultdict(int)
    size_dist = defaultdict(int)
    outcome_counts = defaultdict(int)
    horizon_counts = defaultdict(int)
    cheap_buys = []  # <10¢ for detail
    btc_updown_buys = []

    for t in buys:
        price = float(t.get("price", 0) or 0)
        size = float(t.get("size", 0) or 0)
        usd = size * price
        price_dist[_price_bucket(price)] += 1
        size_dist[_size_bucket(usd)] += 1
        outcome_counts[_infer_outcome(t)] += 1
        horizon_counts[_infer_horizon(t)] += 1
        if price < 0.10:
            cheap_buys.append({"price": price * 100, "usd": usd, "outcome": _infer_outcome(t), "slug": (t.get("slug") or "")[:50]})
        if "up or down" in (t.get("slug") or "").lower() and "bitcoin" in (t.get("title") or t.get("slug") or "").lower():
            btc_updown_buys.append({
                "price": price * 100,
                "usd": usd,
                "outcome": _infer_outcome(t),
                "ts": _parse_ts(t.get("timestamp")),
            })

    # Leaderboard rank
    rank = None
    pnl = 0
    try:
        entries = get_leaderboard(category="CRYPTO", time_period="WEEK", limit=50)
        for i, e in enumerate(entries):
            w = (e.get("proxyWallet") or e.get("proxy_wallet") or "").strip().lower()
            if addr.lower() == w or w.startswith(addr[:10].lower()):
                rank = i + 1
                pnl = float(e.get("pnl", 0) or 0)
                break
    except Exception:
        pass

    return {
        "address": addr,
        "n_trades": len(trades),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "sell_ratio": len(sells) / len(buys) if buys else 0,
        "price_dist": dict(price_dist),
        "size_dist": dict(size_dist),
        "outcome_counts": dict(outcome_counts),
        "horizon_counts": dict(horizon_counts),
        "cheap_buys": cheap_buys[:20],
        "btc_updown_buys": btc_updown_buys,
        "rank_week": rank,
        "pnl_week": pnl,
        "avg_buy_usd": sum(float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0) for t in buys) / len(buys) if buys else 0,
    }


def print_playbook(addr: str, r: dict) -> None:
    """Print inferred strategy playbook."""
    label = f"{addr[:10]}...{addr[-6:]}"
    print(f"\n{'═' * 70}")
    print(f"  {label}  (CRYPTO WEEK #{r['rank_week'] or '?'}  PnL ${r['pnl_week']:,.0f})" if r.get("rank_week") else f"  {label}")
    print("═" * 70)

    print("\n📊 PRICE DISTRIBUTION (where they buy)")
    for bucket in ["0-5¢", "5-20¢", "20-50¢", "50-80¢", "80-100¢"]:
        c = r["price_dist"].get(bucket, 0)
        pct = 100 * c / r["n_buys"] if r["n_buys"] else 0
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"  {bucket:10} {bar} {c:3} ({pct:.0f}%)")

    print("\n💰 SIZE DISTRIBUTION")
    for bucket in ["<$2", "$2-5", "$5-15", "$15-50", "$50+"]:
        c = r["size_dist"].get(bucket, 0)
        pct = 100 * c / r["n_buys"] if r["n_buys"] else 0
        print(f"  {bucket:8} {c:3} ({pct:.0f}%)")
    print(f"  Avg buy: ${r['avg_buy_usd']:.2f}")

    print("\n📈 OUTCOME PREFERENCE (UP vs DOWN in up/down markets)")
    for k, v in sorted(r["outcome_counts"].items(), key=lambda x: -x[1]):
        if k != "?":
            print(f"  {k:4} {v:3}")

    print("\n⏱ HORIZON MIX")
    for k, v in sorted(r["horizon_counts"].items(), key=lambda x: -x[1]):
        print(f"  {k:12} {v:3}")

    print(f"\n🔄 SELL RATIO: {r['sell_ratio']*100:.1f}% (low = hold to resolution)")

    if r["cheap_buys"]:
        print("\n🎯 CHEAP BUYS (<10¢) — sample")
        for b in r["cheap_buys"][:8]:
            print(f"  {b['price']:5.1f}¢  ${b['usd']:5.2f}  {b['outcome']:4}  {b['slug'][:45]}...")

    # Inferred rules
    print("\n📋 INFERRED PLAYBOOK")
    rules = []
    cheap_pct = sum(r["price_dist"].get(b, 0) for b in ["0-5¢", "5-20¢"]) / max(r["n_buys"], 1) * 100
    if cheap_pct > 20:
        rules.append(f"• Buys cheap (<20¢) often ({cheap_pct:.0f}%) — lottery / mispricing plays")
    mid_pct = r["price_dist"].get("20-50¢", 0) / max(r["n_buys"], 1) * 100
    if mid_pct > 30:
        rules.append(f"• Favors 20-50¢ ({mid_pct:.0f}%) — balanced risk/reward")
    rich_pct = sum(r["price_dist"].get(b, 0) for b in ["50-80¢", "80-100¢"]) / max(r["n_buys"], 1) * 100
    if rich_pct > 30:
        rules.append(f"• Buys expensive (50¢+) often ({rich_pct:.0f}%) — near-certainty / terminal")
    if r["sell_ratio"] < 0.2:
        rules.append("• Holds to resolution (rarely sells)")
    up = r["outcome_counts"].get("UP", 0)
    down = r["outcome_counts"].get("DOWN", 0)
    if up + down > 10:
        bias = "UP" if up > down * 1.5 else ("DOWN" if down > up * 1.5 else "balanced")
        rules.append(f"• Outcome bias: {bias} (UP={up}, DOWN={down})")
    if r["horizon_counts"].get("btc_updown", 0) + r["horizon_counts"].get("5min", 0) > r["n_buys"] * 0.5:
        rules.append("• Focus: BTC 5/15-min up/down markets")
    for rule in rules:
        print(f"  {rule}")
    if not rules:
        print("  (no strong patterns)")


def main():
    wallets_env = os.getenv(
        "TRACKED_WALLETS",
        "0x1979ae6b7e6534de9c4539d0c205e582ca637c9d,0x1d0034134e339a309700ff2d34e99fa2d48b0313",
    )
    wallets = [w.strip() for w in wallets_env.split(",") if w.strip()]

    print("=" * 70)
    print("  REVERSE ENGINEERING: Strategy Playbook")
    print("=" * 70)

    for addr in wallets:
        try:
            r = reverse_engineer(addr)
            print_playbook(addr, r)
        except Exception as e:
            print(f"\n  Error for {addr[:10]}...: {e}")

    print("\n" + "=" * 70)
    print("  Compare the two playbooks above to spot shared vs unique patterns.")
    print("=" * 70)


if __name__ == "__main__":
    main()
