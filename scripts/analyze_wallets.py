#!/usr/bin/env python3
"""
Analyze tracked wallets for wallet_copy feasibility.

Outputs: copyability, sell rate, crypto %, trade sizes, overlap, and feasibility
of running all together.

Usage:
  TRACKED_WALLETS=0xabc,0xdef python scripts/analyze_wallets.py
"""

import os
import sys
import time
from collections import defaultdict

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.data_client import get_trades_by_user
from src.wallet_rotation.metrics import compute_wallet_metrics, _categorize_market, _infer_horizon


def _fetch_trades(addr: str, max_trades: int = 300) -> list:
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
        time.sleep(0.15)
    return trades[:max_trades]


def _is_crypto(trade: dict) -> bool:
    title = (trade.get("title") or "").lower()
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    text = f"{title} {slug}"
    return any(kw in text for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "xrp"])


def analyze_wallet(addr: str, min_trade_usd: float = 3) -> dict:
    trades = _fetch_trades(addr)
    buys = [t for t in trades if (t.get("side") or "").upper() == "BUY"]
    sells = [t for t in trades if (t.get("side") or "").upper() == "SELL"]

    crypto_buys = [t for t in buys if _is_crypto(t)]

    def _usd(t):
        return (float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0))

    copyable_buys = [t for t in crypto_buys if _usd(t) >= min_trade_usd]

    sell_ratio = len(sells) / len(buys) if buys else 0
    crypto_pct = 100 * len(crypto_buys) / len(buys) if buys else 0
    copyable_pct = 100 * len(copyable_buys) / len(crypto_buys) if crypto_buys else 0

    buy_usds = [(float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)) for t in buys]
    avg_buy_usd = sum(buy_usds) / len(buy_usds) if buy_usds else 0
    below_min = sum(1 for u in buy_usds if u < min_trade_usd)

    # Unique tokens traded
    tokens_bought = set()
    for t in buys:
        aid = t.get("asset") or t.get("asset_id")
        if aid:
            tokens_bought.add(aid)

    metrics = compute_wallet_metrics(addr, max_trades=200, sleep_between_pages=0.15)
    mm_like = metrics.get("mm_like", False) if metrics else False
    vf_like = metrics.get("volume_farmer_like", False) if metrics else False

    return {
        "addr": addr,
        "n_trades": len(trades),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "sell_ratio": sell_ratio,
        "crypto_pct": crypto_pct,
        "copyable_buys": len(copyable_buys),
        "copyable_pct": copyable_pct,
        "avg_buy_usd": avg_buy_usd,
        "below_min_usd": below_min,
        "tokens_bought": tokens_bought,
        "metrics": metrics,
        "mm_like": mm_like,
        "volume_farmer_like": vf_like,
    }


def main():
    wallets_env = os.getenv(
        "TRACKED_WALLETS",
        "0xe00740bce98a594e26861838885ab310ec3b548c,0x4c353dd347c2e7d8bcdc5cd6ee569de7baf23e2f,"
        "0x63ce342161250d705dc0b16df89036c8e5f9ba9a,0x2d8b401d2f0e6937afebf18e19e11ca568a5260a,"
        "0xd84c2b6d65dc596f49c7b6aadd6d74ca91e407b9",
    )
    wallets = [w.strip() for w in wallets_env.split(",") if w.strip()]
    min_trade_usd = float(os.getenv("WALLET_COPY_MIN_TRADE_USD", "3"))

    print("=" * 75)
    print("  WALLET COPY FEASIBILITY ANALYSIS")
    print("=" * 75)
    print(f"  Wallets: {len(wallets)}  |  Min trade USD: {min_trade_usd}  |  Crypto-only: assumed")
    print("=" * 75)

    results = []
    all_tokens: dict[str, set[str]] = defaultdict(set)

    for addr in wallets:
        try:
            r = analyze_wallet(addr, min_trade_usd)
            results.append(r)
            for tok in r["tokens_bought"]:
                all_tokens[tok].add(addr[:10] + "..")
        except Exception as e:
            print(f"\n  Error {addr[:12]}...: {e}")
            results.append({"addr": addr, "error": str(e)})

    # Per-wallet summary
    print("\n📊 PER-WALLET SUMMARY")
    print("-" * 75)
    for r in results:
        if "error" in r:
            print(f"  {r['addr'][:12]}...  ERROR: {r['error']}")
            continue
        short = f"{r['addr'][:8]}..{r['addr'][-6:]}"
        sell_pct = r["sell_ratio"] * 100
        m = r.get("metrics") or {}
        days_inactive = m.get("days_since_last_trade", 999)
        trades_day = m.get("trades_per_day", 0)

        mm = r.get("mm_like", False)
        vf = r.get("volume_farmer_like", False)
        avoid = "❌ AVOID" if (mm or vf) else ("✅" if r["copyable_buys"] > 0 and r["crypto_pct"] >= 50 else "⚠️")
        print(f"\n  {avoid} {short}")
        if mm:
            print(f"     ⛔ MM/Spread-like — balanced buys/sells, high frequency")
        if vf:
            print(f"     ⛔ Volume-farmer-like — high trades, small size, many tokens")
        print(f"     Trades: {r['n_trades']} (BUY {r['n_buys']} / SELL {r['n_sells']})")
        print(f"     Sell rate: {sell_pct:.1f}%  |  Crypto: {r['crypto_pct']:.0f}%")
        print(f"     Copyable crypto BUYs (≥${min_trade_usd}): {r['copyable_buys']} ({r['copyable_pct']:.0f}% of crypto)")
        print(f"     Avg BUY: ${r['avg_buy_usd']:.2f}  |  Below min: {r.get('below_min_usd', 0)}")
        print(f"     Trades/day: {trades_day:.1f}  |  Days since last: {days_inactive:.1f}")

    # Overlap: tokens traded by multiple wallets
    overlap = {k: v for k, v in all_tokens.items() if len(v) > 1}
    print("\n\n📈 TOKEN OVERLAP (same token traded by multiple wallets)")
    print("-" * 75)
    if overlap:
        by_count = sorted(overlap.items(), key=lambda x: -len(x[1]))
        print(f"  {len(overlap)} tokens traded by 2+ wallets (risk: conflicting SELL signals)")
        for tok, wallets_set in by_count[:15]:
            print(f"    {tok[:20]}...  ← {len(wallets_set)} wallets: {', '.join(sorted(wallets_set))}")
    else:
        print("  No overlap — wallets trade different tokens.")

    # Feasibility
    print("\n\n🔧 FEASIBILITY: RUNNING ALL 5 TOGETHER")
    print("-" * 75)
    avoid = [r for r in results if "error" not in r and (r.get("mm_like") or r.get("volume_farmer_like"))]
    ok = [r for r in results if "error" not in r and r.get("copyable_buys", 0) > 0 and not r.get("mm_like") and not r.get("volume_farmer_like")]
    crypto_ok = [r for r in ok if r.get("crypto_pct", 0) >= 50]
    active = [r for r in ok if (r.get("metrics") or {}).get("days_since_last_trade", 999) < 7]

    if avoid:
        print(f"  ⛔ MM/Volume-farmer (avoid): {len(avoid)}")
    print(f"  Copyable (crypto BUY ≥${min_trade_usd}, not MM/vf): {len(ok)}/{len(wallets)}")
    print(f"  Crypto-heavy (≥50%): {len(crypto_ok)}/{len(wallets)}")
    print(f"  Active (trade in last 7d): {len(active)}/{len(wallets)}")
    print(f"  Token overlap: {len(overlap)} tokens (conflicting exits possible)")
    print()
    print("  API: 5 wallets × min_wallet_poll_seconds=2 → ~10s per full cycle (fine)")
    print("  Risk: When A sells but B holds same token → we exit full position (by design)")
    print()
    if len(crypto_ok) >= 4 and len(active) >= 3:
        print("  ✅ FEASIBLE — most wallets copyable, crypto-focused, active")
    elif len(ok) >= 3:
        print("  ⚠️ PARTIAL — some wallets may not copy much (low crypto or inactive)")
    else:
        print("  ❌ RISKY — few copyable wallets; consider fewer or different wallets")

    print("\n" + "=" * 75)


if __name__ == "__main__":
    main()
