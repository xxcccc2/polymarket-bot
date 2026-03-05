"""
Data downloader for PolyBackTest API.

Fetches markets and snapshots within free-plan limits and stores locally.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..logging_utils import cprint

from .polybacktest_client import PolyBackTestClient, PolyBackTestError
from .store import BacktestStore

# Free plan limits per market type
FREE_PLAN_LIMITS: Dict[str, int] = {
    "5m": 50,
    "15m": 50,
    "1hr": 24,
    "4hr": 24,
    "24hr": 5,
}


class DataDownloader:
    """
    Download PolyBackTest data and store locally.

    Respects free plan limits.
    """

    def __init__(
        self,
        client: Optional[PolyBackTestClient] = None,
        store: Optional[BacktestStore] = None,
    ):
        self.client = client or PolyBackTestClient()
        self.store = store or BacktestStore()

    def download(
        self,
        coin: str = "btc",
        market_types: Optional[List[str]] = None,
        include_orderbook: bool = False,
    ) -> Dict[str, Any]:
        """
        Download markets and snapshots for given types.

        Args:
            coin: btc or eth
            market_types: e.g. ["5m", "15m"] or None for all
            include_orderbook: Include full orderbook in snapshots (larger)

        Returns:
            Summary dict with counts and any warnings
        """
        types = market_types or list(FREE_PLAN_LIMITS.keys())
        summary: Dict[str, Any] = {
            "markets_downloaded": 0,
            "snapshots_downloaded": 0,
            "errors": [],
            "warnings": [],
        }

        # Optionally check limits (may 404 on free tier)
        try:
            limits_resp = self.client.get_limits()
            plan = limits_resp.get("plan", "free")
            summary["plan"] = plan
        except PolyBackTestError as e:
            if e.status_code == 404:
                summary["warnings"].append("Limits endpoint not available, using defaults")
            else:
                raise

        for mt in types:
            limit = FREE_PLAN_LIMITS.get(mt, 50)
            cprint(f"\n  Fetching {mt} markets (limit={limit})...", "cyan")

            try:
                resp = self.client.list_markets(
                    coin=coin,
                    market_type=mt,
                    limit=limit,
                    offset=0,
                    resolved=True,  # Prefer resolved for backtesting
                )
            except PolyBackTestError as e:
                summary["errors"].append(f"{mt}: {e}")
                continue

            markets = resp.get("markets", [])
            warning = resp.get("warning")
            if warning:
                summary["warnings"].append(warning)

            if not markets:
                cprint(f"    No {mt} markets found", "yellow")
                continue

            # Normalize and add coin to each market
            for m in markets:
                m["coin"] = coin
                if "market_id" not in m and "id" in m:
                    m["market_id"] = str(m["id"])

            self.store.save_markets(markets)
            summary["markets_downloaded"] += len(markets)
            cprint(f"    Saved {len(markets)} markets", "green")

            # Fetch snapshots for each market
            for i, m in enumerate(markets):
                market_id = str(m.get("market_id", ""))
                if not market_id:
                    continue
                try:
                    snap_count = self._download_snapshots(
                        market_id=market_id,
                        coin=coin,
                        include_orderbook=include_orderbook,
                    )
                    summary["snapshots_downloaded"] += snap_count
                    if (i + 1) % 10 == 0 or i == len(markets) - 1:
                        cprint(
                            f"    Snapshots: {i + 1}/{len(markets)} markets",
                            "dark_grey",
                        )
                except PolyBackTestError as e:
                    if e.status_code == 402:
                        summary["warnings"].append(
                            f"Market {market_id} outside plan (402)"
                        )
                    else:
                        summary["errors"].append(f"{market_id}: {e}")

        return summary

    def _download_snapshots(
        self,
        market_id: str,
        coin: str,
        include_orderbook: bool = False,
    ) -> int:
        """Download all snapshots for a market (paginated)."""
        all_snapshots: List[Dict[str, Any]] = []
        offset = 0
        limit = 1000

        while True:
            resp = self.client.get_snapshots(
                market_id=market_id,
                coin=coin,
                limit=limit,
                offset=offset,
                include_orderbook=include_orderbook,
            )
            snapshots = resp.get("snapshots", [])
            if not snapshots:
                break
            all_snapshots.extend(snapshots)
            if len(snapshots) < limit:
                break
            offset += limit

        if all_snapshots:
            self.store.save_snapshots(market_id, all_snapshots)

        return len(all_snapshots)
