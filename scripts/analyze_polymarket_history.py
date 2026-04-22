#!/usr/bin/env python3
import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

MARKET_PATTERN = re.compile(r"^Bitcoin Up or Down - ([A-Za-z]+ \d{1,2}), (\d{1,2})(?:(\d{2}))?(AM|PM) ET$")


@dataclass
class MarketResult:
    market_name: str
    market_date: date
    weekday: str
    is_weekend: bool
    hour_et: int
    buy_cost: float
    sell_proceeds: float
    redeem_proceeds: float
    pnl: float
    has_exit_activity: bool


def infer_year(path: Path) -> int:
    match = re.search(r"(20\d{2})-\d{2}-\d{2}", path.name)
    if match:
        return int(match.group(1))
    return datetime.now().year


def parse_market_metadata(name: str, year: int):
    match = MARKET_PATTERN.match(name)
    if not match:
        return None
    market_date = datetime.strptime(f"{year} {match.group(1)}", "%Y %B %d").date()
    hour12 = int(match.group(2))
    ampm = match.group(4)
    hour24 = hour12 % 12
    if ampm == "PM":
        hour24 += 12
    return market_date, hour24


def load_market_results(csv_path: Path, days: int, year: int | None) -> list[MarketResult]:
    resolved_year = year or infer_year(csv_path)
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            meta = parse_market_metadata(row["marketName"], resolved_year)
            if meta is None:
                continue
            row["usdcAmount"] = float(row["usdcAmount"] or 0)
            row["tokenAmount"] = float(row["tokenAmount"] or 0)
            row["timestamp"] = int(row["timestamp"] or 0)
            row["market_date"], row["hour_et"] = meta
            rows.append(row)

    if not rows:
        return []

    max_date = max(row["market_date"] for row in rows)
    min_date = max_date.fromordinal(max_date.toordinal() - max(days - 1, 0))
    rows = [row for row in rows if min_date <= row["market_date"] <= max_date]
    rows.sort(key=lambda row: (row["timestamp"], row["hash"], row["action"]))

    grouped: dict[str, dict] = {}
    for row in rows:
        bucket = grouped.setdefault(
            row["marketName"],
            {
                "market_date": row["market_date"],
                "hour_et": row["hour_et"],
                "buy_cost": 0.0,
                "sell_proceeds": 0.0,
                "redeem_proceeds": 0.0,
                "has_exit_activity": False,
            },
        )
        action = row["action"].strip().lower()
        if action == "buy":
            bucket["buy_cost"] += row["usdcAmount"]
        elif action == "sell":
            bucket["sell_proceeds"] += row["usdcAmount"]
            bucket["has_exit_activity"] = True
        elif action == "redeem":
            bucket["redeem_proceeds"] += row["usdcAmount"]
            bucket["has_exit_activity"] = True

    results: list[MarketResult] = []
    for market_name, bucket in sorted(grouped.items(), key=lambda item: (item[1]["market_date"], item[1]["hour_et"], item[0])):
        market_date = bucket["market_date"]
        weekday = market_date.strftime("%A")
        pnl = bucket["sell_proceeds"] + bucket["redeem_proceeds"] - bucket["buy_cost"]
        results.append(
            MarketResult(
                market_name=market_name,
                market_date=market_date,
                weekday=weekday,
                is_weekend=market_date.weekday() >= 5,
                hour_et=bucket["hour_et"],
                buy_cost=bucket["buy_cost"],
                sell_proceeds=bucket["sell_proceeds"],
                redeem_proceeds=bucket["redeem_proceeds"],
                pnl=pnl,
                has_exit_activity=bucket["has_exit_activity"],
            )
        )
    return results


def summarize(results: Iterable[MarketResult]) -> tuple[int, float, float, int, int, int]:
    items = list(results)
    count = len(items)
    total = sum(item.pnl for item in items)
    avg = total / count if count else 0.0
    wins = sum(1 for item in items if item.pnl > 1e-9)
    losses = sum(1 for item in items if item.pnl < -1e-9)
    flats = count - wins - losses
    return count, total, avg, wins, losses, flats


def print_report(results: list[MarketResult], csv_path: Path, days: int) -> None:
    print(f"CSV: {csv_path}")
    print(f"Hourly BTC markets analyzed: {len(results)}")
    print(f"Window: last {days} day(s) ending {max((r.market_date for r in results), default='n/a')}")
    print(f"Total realized PnL: {sum(r.pnl for r in results):+.4f} USDC")
    print()
    print("Recent hourly markets")
    for item in results:
        print(
            f"{item.market_date.isoformat()} {item.hour_et:02d}:00 ET | {item.weekday:<9} | "
            f"pnl={item.pnl:+.4f} | buy={item.buy_cost:.4f} sell={item.sell_proceeds:.4f} redeem={item.redeem_proceeds:.4f} | {item.market_name}"
        )
    print()

    weekday_stats = summarize([item for item in results if not item.is_weekend])
    weekend_stats = summarize([item for item in results if item.is_weekend])
    print("Weekday vs weekend")
    print(
        f"Weekday | markets={weekday_stats[0]} total_pnl={weekday_stats[1]:+.4f} avg_pnl={weekday_stats[2]:+.4f} "
        f"wins={weekday_stats[3]} losses={weekday_stats[4]} flat={weekday_stats[5]}"
    )
    print(
        f"Weekend | markets={weekend_stats[0]} total_pnl={weekend_stats[1]:+.4f} avg_pnl={weekend_stats[2]:+.4f} "
        f"wins={weekend_stats[3]} losses={weekend_stats[4]} flat={weekend_stats[5]}"
    )
    print()

    print("By weekday")
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        items = [item for item in results if item.weekday == day]
        if not items:
            continue
        stats = summarize(items)
        print(
            f"{day:<9} | markets={stats[0]} total_pnl={stats[1]:+.4f} avg_pnl={stats[2]:+.4f} "
            f"wins={stats[3]} losses={stats[4]}"
        )
    print()

    by_hour: dict[int, list[MarketResult]] = defaultdict(list)
    for item in results:
        by_hour[item.hour_et].append(item)
    print("By hour ET")
    for hour in sorted(by_hour):
        stats = summarize(by_hour[hour])
        print(
            f"{hour:02d}:00 ET | markets={stats[0]} total_pnl={stats[1]:+.4f} avg_pnl={stats[2]:+.4f} "
            f"wins={stats[3]} losses={stats[4]}"
        )
    print()

    ranked = []
    for hour, items in by_hour.items():
        stats = summarize(items)
        ranked.append((stats[2], stats[1], hour, stats[0]))
    ranked.sort(reverse=True)
    print("Best hours by avg PnL")
    for avg_pnl, total_pnl, hour, count in ranked[:5]:
        print(f"{hour:02d}:00 ET | avg_pnl={avg_pnl:+.4f} total_pnl={total_pnl:+.4f} markets={count}")
    print()
    print("Worst hours by avg PnL")
    for avg_pnl, total_pnl, hour, count in sorted(ranked)[:5]:
        print(f"{hour:02d}:00 ET | avg_pnl={avg_pnl:+.4f} total_pnl={total_pnl:+.4f} markets={count}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--include-open", action="store_true")
    args = parser.parse_args()

    results = load_market_results(args.csv_path, days=args.days, year=args.year)
    if not args.include_open:
        results = [result for result in results if result.has_exit_activity]
    if not results:
        print("No BTC hourly markets found in the requested window.")
        return 1
    print_report(results, args.csv_path, args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
