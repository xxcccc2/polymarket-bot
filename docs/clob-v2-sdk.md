# Polymarket CLOB SDK v2

Live trading uses **[py-clob-client-v2](https://pypi.org/project/py-clob-client-v2/)** (import name `py_clob_client_v2`). The legacy **py-clob-client** (v1) package is **not** supported by this codebase.

Install with the rest of the stack:

```bash
pip install -r requirements.txt
```

If the client is missing at runtime, startup logs show:

`py-clob-client-v2 not installed. Run: pip install py-clob-client-v2`

## Endpoints

Default HTTP and WebSocket bases match Polymarket’s public CLOB infrastructure (see `src/config.py`):

- `CLOB_HOST` — `https://clob.polymarket.com`
- Market user stream — configured in `websocket_feed` (see `WS_URL` / user stream URL in config)

Override only if Polymarket documents a different environment for your integration.

## Optional: builder attribution

If Polymarket provides a **builder code** for your integration, set `POLYMARKET_BUILDER_CODE` in `.env`. When present and the installed SDK exposes `BuilderConfig`, the client passes `builder_config=` into `ClobClient` (`src/client.py`).

## Session setup (authenticated client)

`PolymarketClient.connect()` uses a **two-step** pattern required by the v2 client:

1. Build a `ClobClient` **without** API credentials.
2. Call `create_or_derive_api_key()` to obtain L2 API credentials.
3. Build a **second** `ClobClient` instance **with** those credentials attached.

That second instance is used for order placement, balances, and exposes credentials for the **authenticated user WebSocket** (`get_api_credentials()`).

## Order placement

- **Single orders** — `create_and_post_order(order_args, options, order_type, post_only=...)`.
- **Per-market options** — `PartialCreateOrderOptions(tick_size=..., neg_risk=...)` is built from cached `get_tick_size` / `get_neg_risk` results (see `_get_order_create_options` in `src/client.py`).
- **`OrderArgs` and fees** — The v2 SDK’s `OrderArgs` does **not** accept `fee_rate_bps`. The bot’s `place_order(..., fee_rate_bps=...)` parameter is kept for call-site compatibility but **is not forwarded** into `OrderArgs`; fee handling is left to the exchange. Order metadata in SQLite may still record `fee_rate_bps` for accounting and reconciliation.

## Batch posting

`place_orders_batch` signs each leg with `create_order`, wraps results in **`PostOrdersV2Args`** when available (fallback: `PostOrdersArgs`), then submits via `post_orders(...)`.

## Cancels

Cancellations call `cancel_order(OrderPayload(orderID=order_id))` when the v2 client exposes `cancel_order` with that signature.

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| `ImportError` / “py-clob-client-v2 not installed” | `pip install py-clob-client-v2` inside the same venv you use for `python -m src.bot`. |
| `AttributeError` involving `tick_size` / options | Confirm your installed `py-clob-client-v2` version matches `requirements.txt`; the bot expects typed `PartialCreateOrderOptions` from the current SDK. |
| `TypeError` on `OrderArgs(..., fee_rate_bps=...)` | Remove `fee_rate_bps` from any custom code that constructs `OrderArgs` directly; only use the kwargs supported by your installed v2 release. |
| 401s on REST after long runs | The client refreshes API credentials on auth errors; if issues persist, verify clock skew, network proxies, and timeouts (`TRADE_FETCH_TIMEOUT_SECONDS`, etc.). |

For project-level tips (allowance, WebSocket noise, risk), see **Wallet-Copy Troubleshooting** in the root [README](../README.md).
