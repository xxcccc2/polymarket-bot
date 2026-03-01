#!/usr/bin/env python3
"""
Discover and score Polymarket wallets for wallet_copy strategy.

Usage:
  python scripts/discover_wallets.py
  # Options:
  TOP_N=10 python scripts/discover_wallets.py
  MIN_CRYPTO_PCT=80 python scripts/discover_wallets.py
  OUTPUT_CONFIG=./config/settings.discovered.example python scripts/discover_wallets.py

Note: If you hit proxy errors, run with proxies disabled:
  unset http_proxy https_proxy; python scripts/discover_wallets.py
"""

import os
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# Avoid proxy blocking Data API
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data_client import get_trades_by_user, get_positions, get_leaderboard


# ---------------------------------------------------------------------------
# Config (env overrides)
# ---------------------------------------------------------------------------
LEADERBOARD_TOP_N = int(os.getenv("LEADERBOARD_TOP_N", "30"))
LEADERBOARD_CATEGORY = os.getenv("LEADERBOARD_CATEGORY", "CRYPTO")
MIN_CRYPTO_PCT = float(os.getenv("MIN_CRYPTO_PCT", "70"))
MIN_SHORTTERM_PCT = float(os.getenv("MIN_SHORTTERM_PCT", "40"))
MAX_HOURS_EXPIRY = float(os.getenv("MAX_HOURS_EXPIRY", "4"))
MIN_TRADES = int(os.getenv("MIN_TRADES", "20"))
MAX_DAYS_SINCE_LAST_TRADE = float(os.getenv("MAX_DAYS_SINCE_LAST_TRADE", "7"))
OUTPUT_TOP_N = int(os.getenv("OUTPUT_TOP_N", "10"))
OUTPUT_CONFIG = os.getenv("OUTPUT_CONFIG", "")
TRADES_PER_WALLET = int(os.getenv("TRADES_PER_WALLET", "200"))

# Scoring weights
W_RANK_DAILY = float(os.getenv("W_RANK_DAILY", "0.35"))
W_RANK_MONTHLY = float(os.getenv("W_RANK_MONTHLY", "0.35"))
W_CRYPTO_PCT = float(os.getenv("W_CRYPTO_PCT", "0.15"))
W_SHORTTERM_PCT = float(os.getenv("W_SHORTTERM_PCT", "0.15"))


# ---------------------------------------------------------------------------
# Helpers (reuse from analyze_wallets)
# ---------------------------------------------------------------------------
def _categorize_market(trade: dict) -> str:
    """Infer market category from trade metadata."""
    title = (trade.get("title") or "").lower()
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    text = f"{title} {slug}"
    if any(kw in text for kw in ["bitcoin", "btc"]):
        if any(kw in text for kw in ["up or down", "updown", "5m", "15m", "1h"]):
            return "btc_shortterm"
        return "btc"
    if any(kw in text for kw in ["ethereum", "eth", "solana", "sol", "xrp"]):
        return "crypto"
    if any(kw in text for kw in ["trump", "biden", "election", "president"]):
        return "politics"
    if any(kw in text for kw in ["sport", "nfl", "nba", "mlb", "game"]):
        return "sports"
    return "other"


def _infer_horizon(trade: dict) -> str:
    """Infer time horizon from slug."""
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    if "5m" in slug or "5 min" in slug:
        return "5min"
    if "15m" in slug or "15 min" in slug:
        return "15min"
    if "1h" in slug or "1 hour" in slug:
        return "1h"
    if "day" in slug or "daily" in slug:
        return "daily"
    if "week" in slug or "month" in slug or "year" in slug or "2025" in slug or "2026" in slug:
        return "longterm"
    return "unknown"


def _is_shortterm(horizon: str) -> bool:
    """Markets with ≤4h to expiry."""
    return horizon in ("5min", "15min", "1h", "daily")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def fetch_candidate_wallets() -> List[Dict]:
    """Fetch leaderboard from DAY and MONTH, union by wallet."""
    by_addr: Dict[str, Dict] = {}

    for period in ("DAY", "MONTH"):
        try:
            entries = get_leaderboard(
                category=LEADERBOARD_CATEGORY,
                time_period=period,
                order_by="PNL",
                limit=LEADERBOARD_TOP_N,
            )
            for i, e in enumerate(entries):
                addr = (e.get("proxyWallet") or e.get("proxy_wallet") or "").strip()
                if not addr or not addr.startswith("0x"):
                    continue
                key = addr.lower()
                if key not in by_addr:
                    by_addr[key] = {
                        "address": addr,
                        "userName": e.get("userName") or e.get("user_name") or addr[:12],
                        "rank_daily": None,
                        "rank_monthly": None,
                        "pnl_daily": None,
                        "pnl_monthly": None,
                        "vol_daily": None,
                        "vol_monthly": None,
                    }
                c = by_addr[key]
                if period == "DAY":
                    c["rank_daily"] = i + 1
                    c["pnl_daily"] = float(e.get("pnl", 0) or 0)
                    c["vol_daily"] = float(e.get("vol", 0) or 0)
                else:
                    c["rank_monthly"] = i + 1
                    c["pnl_monthly"] = float(e.get("pnl", 0) or 0)
                    c["vol_monthly"] = float(e.get("vol", 0) or 0)
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ Leaderboard {period} failed: {e}")

    return list(by_addr.values())


def analyze_wallet_for_scoring(addr: str) -> Optional[Dict]:
    """Fetch trades and compute metrics for scoring."""
    addr = addr.strip()
    if not addr or not addr.startswith("0x"):
        return None

    trades = []
    offset = 0
    while len(trades) < TRADES_PER_WALLET:
        try:
            batch = get_trades_by_user(addr, limit=100, offset=offset, taker_only=False)
        except Exception:
            return None
        if not batch:
            break
        trades.extend(batch)
        if len(batch) < 100:
            break
        offset += len(batch)
        time.sleep(0.2)
    trades = trades[:TRADES_PER_WALLET]

    if not trades:
        return None

    categories = defaultdict(int)
    horizons = defaultdict(int)
    buy_values = []
    last_ts = 0.0

    for t in trades:
        cat = _categorize_market(t)
        categories[cat] += 1
        hor = _infer_horizon(t)
        horizons[hor] += 1
        ts_val = t.get("timestamp", 0)
        if ts_val:
            try:
                ts_num = float(ts_val)
                if ts_num > 1e12:
                    ts_num /= 1000
                last_ts = max(last_ts, ts_num)
            except (TypeError, ValueError):
                pass
        if (t.get("side") or "").upper() == "BUY":
            size = float(t.get("size", 0) or 0)
            price = float(t.get("price", 0) or 0)
            buy_values.append(size * price)

    crypto_cats = ("btc_shortterm", "btc", "crypto")
    crypto_count = sum(categories.get(c, 0) for c in crypto_cats)
    crypto_pct = (crypto_count / len(trades)) * 100 if trades else 0

    shortterm_horizons = ("5min", "15min", "1h", "daily")
    shortterm_count = sum(horizons.get(h, 0) for h in shortterm_horizons)
    shortterm_pct = (shortterm_count / len(trades)) * 100 if trades else 0

    # Assume trades span ~7–30 days if we have enough
    days_span = max(7, len(trades) / 10)
    trades_per_day = len(trades) / days_span if days_span else 0

    days_since_last = (time.time() - last_ts) / 86400 if last_ts else 999

    return {
        "address": addr,
        "n_trades": len(trades),
        "crypto_pct": crypto_pct,
        "shortterm_pct": shortterm_pct,
        "avg_buy_usd": sum(buy_values) / len(buy_values) if buy_values else 0,
        "trades_per_day": trades_per_day,
        "days_since_last_trade": days_since_last,
        "categories": dict(categories),
        "horizons": dict(horizons),
    }


def score_wallet(
    c: Dict,
    analysis: Optional[Dict],
) -> Tuple[float, List[str]]:
    """
    Compute score 0–1 and list of reasons.
    """
    reasons = []
    score = 0.0

    if not analysis:
        return 0.0, ["No trade data"]

    # Filter: min trades
    if analysis["n_trades"] < MIN_TRADES:
        return 0.0, [f"Too few trades ({analysis['n_trades']} < {MIN_TRADES})"]

    # Filter: crypto %
    if analysis["crypto_pct"] < MIN_CRYPTO_PCT:
        return 0.0, [f"Low crypto % ({analysis['crypto_pct']:.0f}% < {MIN_CRYPTO_PCT}%)"]

    # Filter: short-term %
    if analysis["shortterm_pct"] < MIN_SHORTTERM_PCT:
        return 0.0, [f"Low short-term % ({analysis['shortterm_pct']:.0f}% < {MIN_SHORTTERM_PCT}%)"]

    # Filter: stale
    if analysis["days_since_last_trade"] > MAX_DAYS_SINCE_LAST_TRADE:
        return 0.0, [f"Inactive ({analysis['days_since_last_trade']:.0f}d since last trade)"]

    # Rank score (daily)
    r_daily = c.get("rank_daily")
    if r_daily is not None:
        rank_score_daily = max(0, 1.0 - (r_daily - 1) / 50)
        score += W_RANK_DAILY * rank_score_daily
        reasons.append(f"daily#{r_daily}")
    else:
        reasons.append("no daily rank")

    # Rank score (monthly)
    r_monthly = c.get("rank_monthly")
    if r_monthly is not None:
        rank_score_monthly = max(0, 1.0 - (r_monthly - 1) / 50)
        score += W_RANK_MONTHLY * rank_score_monthly
        reasons.append(f"month#{r_monthly}")
    else:
        reasons.append("no month rank")

    # Crypto %
    crypto_norm = min(1.0, analysis["crypto_pct"] / 100)
    score += W_CRYPTO_PCT * crypto_norm

    # Short-term %
    shortterm_norm = min(1.0, analysis["shortterm_pct"] / 100)
    score += W_SHORTTERM_PCT * shortterm_norm

    return min(1.0, score), reasons


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Polymarket Wallet Discovery")
    print("=" * 70)
    print(f"  Category: {LEADERBOARD_CATEGORY}")
    print(f"  Min crypto %: {MIN_CRYPTO_PCT}% | Min short-term %: {MIN_SHORTTERM_PCT}%")
    print(f"  Min trades: {MIN_TRADES} | Max days inactive: {MAX_DAYS_SINCE_LAST_TRADE}")
    print()

    # 1. Fetch candidates
    print("Fetching leaderboard (DAY + MONTH)...")
    candidates = fetch_candidate_wallets()
    print(f"  Found {len(candidates)} unique wallets")
    if not candidates:
        print("  No candidates. Exiting.")
        return

    # 2. Analyze each
    print("\nAnalyzing trades...")
    results = []
    for i, c in enumerate(candidates):
        addr = c["address"]
        print(f"  [{i+1}/{len(candidates)}] {addr[:10]}...{addr[-6:]}...", end=" ", flush=True)
        analysis = analyze_wallet_for_scoring(addr)
        score, reasons = score_wallet(c, analysis)
        c["analysis"] = analysis
        c["score"] = score
        c["reasons"] = reasons
        if score > 0:
            results.append(c)
            print(f"score={score:.2f} ✓")
        else:
            print(f"filtered: {reasons[0]}")
        time.sleep(0.3)

    # 3. Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:OUTPUT_TOP_N]

    # 4. Output
    print("\n" + "=" * 70)
    print(f"Top {len(top)} Wallets (by score)")
    print("=" * 70)

    if not top:
        print("  No wallets passed filters.")
        return

    # Table
    print(f"\n{'Rank':<5} {'Score':<6} {'Wallet':<18} {'Crypto%':<8} {'Short%':<8} {'Trades':<7} {'d#':<5} {'m#':<5}")
    print("-" * 70)
    for i, r in enumerate(top, 1):
        a = r.get("analysis") or {}
        r_d = r.get("rank_daily") or "-"
        r_m = r.get("rank_monthly") or "-"
        print(
            f"{i:<5} {r['score']:.2f}   {r['address'][:10]}...{r['address'][-6:]}  "
            f"{a.get('crypto_pct', 0):.0f}%     {a.get('shortterm_pct', 0):.0f}%     "
            f"{a.get('n_trades', 0):<7} {r_d!s:<5} {r_m!s:<5}"
        )

    # TRACKED_WALLETS line
    wallets_str = ",".join(r["address"] for r in top)
    print("\n" + "-" * 70)
    print("TRACKED_WALLETS (copy to config):")
    print(wallets_str)
    print("-" * 70)

    # Optional: write config file
    if OUTPUT_CONFIG:
        try:
            with open(OUTPUT_CONFIG, "w") as f:
                f.write("# Auto-discovered wallets (run discover_wallets.py to refresh)\n")
                f.write(f"TRACKED_WALLETS={wallets_str}\n")
            print(f"\nWrote {OUTPUT_CONFIG}")
        except Exception as e:
            print(f"\n⚠️ Could not write config: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
