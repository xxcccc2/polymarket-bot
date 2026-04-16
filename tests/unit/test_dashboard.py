from __future__ import annotations

from rich.console import Console

from src.dashboard import (
    BinanceSnapshot,
    Dashboard,
    DashboardState,
    ExecutionHealthSnapshot,
    MarketQualitySnapshot,
    OpenOrderRow,
    PortfolioSnapshot,
    RecentFillRow,
    StrategyDetailRow,
    StrategyRow,
)


def test_dashboard_render_includes_new_panels():
    dashboard = Dashboard()
    state = DashboardState(
        paper=True,
        n_markets=12,
        scan_number=5,
        binance=BinanceSnapshot(connected=True, price=100000.0),
        portfolio=PortfolioSnapshot(balance=1200.0, start_balance=1000.0, active_orders=2),
        execution_health=ExecutionHealthSnapshot(market_ws_connected=True, user_ws_connected=True),
        market_quality=MarketQualitySnapshot(
            total_tokens=10,
            real_quote_tokens=8,
            missing_quote_tokens=2,
            eligible_ml=2,
            eligible_terminal=1,
            eligible_combo=4,
        ),
        strategies=[
            StrategyRow(
                name="ml_directional:15m",
                signals=2,
                trades=1,
                healthy=True,
                last_signal="5s ago",
                status="2 live signals",
            )
        ],
        open_orders=[
            OpenOrderRow(
                strategy="ml_directional",
                market="btc-updown",
                outcome="YES",
                price=0.55,
                size=12.0,
                age_seconds=10.0,
                status="open",
            )
        ],
        recent_fills=[
            RecentFillRow(
                strategy="ml_directional",
                market="btc-updown",
                side="BUY",
                price=0.55,
                size=12.0,
                age_seconds=4.0,
            )
        ],
        strategy_details=[
            StrategyDetailRow(
                name="ml_directional:15m",
                summary="edge 3.1c < 4.0c",
                detail="model v1 | acc 54.00% | brier 0.220 | pending 1",
            )
        ],
    )

    rendered = dashboard._render(state)
    console = Console(record=True, width=140)
    console.print(rendered)
    output = console.export_text()

    assert "Execution Health" in output
    assert "Market Data Quality" in output
    assert "Open Orders" in output
    assert "Recent Fills" in output
    assert "Strategy Detail" in output
    assert "Health" in output
    assert "Eligible" in output
