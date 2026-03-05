# PolyBackTest DB Exports

Exports from `polybacktest.db` for inspection and analysis.

## Files

| File | Rows | Description |
|------|------|-------------|
| `markets.csv` | 147 | All markets (market_id, slug, type, times, btc_price_start/end, winner, tokens) |
| `snapshots_summary.csv` | 133 | Per-market snapshot counts and time ranges |
| `snapshots_sample_5m.csv` | 500 | Sample snapshots from one 5m market |
| `snapshots_full.csv` | ~420k | All snapshots (id, market_id, time, btc_price, price_up, price_down) |

## Viewing

- **Spreadsheet:** Open CSV in Excel, Numbers, or Google Sheets
- **Terminal:** `head -50 markets.csv` or `column -s, -t < markets.csv | less -S`
- **SQLite:** Query directly: `sqlite3 ../polybacktest.db "SELECT * FROM markets LIMIT 10;"`

## WAL/SHM Files

`polybacktest.db-wal` and `polybacktest.db-shm` are **normal** when using SQLite WAL mode:

- **WAL** = Write-Ahead Log — uncommitted changes before checkpoint
- **SHM** = Shared memory — coordination between connections

They appear when the DB is written to and can disappear after `PRAGMA wal_checkpoint(TRUNCATE)` or when all connections close and a checkpoint runs. The store uses WAL for better concurrent read/write. This is expected behavior, not a bug.

To consolidate and reduce WAL churn after downloads:

```bash
sqlite3 data/backtest/polybacktest.db "PRAGMA wal_checkpoint(TRUNCATE);"
```
