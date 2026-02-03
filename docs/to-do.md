# Polymarket Bot - To-Do List

**Last Updated:** February 2025

---

## 🔴 High Priority

### Infrastructure
- [ ] **Persistence Layer:** Add SQLite for orders/positions/trades (state lost on restart)
- [ ] **Balance Tracking:** Fix `client.py:get_balance()` - currently returns None
- [ ] **Unit Tests:** Add pytest tests for strategies, risk_manager, order_manager
- [ ] **Proper Logging:** Replace termcolor with Python `logging` module
- [ ] **Market Data Refresh:** Markets only fetched once at startup - need periodic refresh

### Strategies
- [ ] **Cross-Platform Arbitrage (Kalshi):** Integrate Kalshi API for cross-venue arb
- [ ] **Combinatorial Arbitrage:** NLP-based detection of logical dependencies

---

## 🟡 Medium Priority

### Infrastructure
- [ ] **WebSocket Feed:** Test and enable (currently disabled, using REST polling)
- [ ] **Retry Logic:** Add exponential backoff for API failures
- [ ] **Fill Detection:** Use WebSocket events instead of polling every 10s

### Strategies (see STRATEGY_ROADMAP.md)
- [ ] **Anchoring Bias Strategy:** Trade away from psychological price levels
- [ ] **Overreaction Strategy:** Mean-reversion after extreme moves
- [ ] **Time Decay Strategy:** Long-dated contract compression toward 50%
- [ ] **Liquidity Provision:** Maximize Q-score for platform rewards

### UI/Dashboard
- [ ] **Static CLI Dashboard:** No infinite looping prints - show fixed dashboard layout
  - Use `curses` or `rich` library for terminal UI
  - Display: positions, P&L, active orders, strategy status
  - Update in-place instead of scrolling output
- [ ] **Optional GUI:** Consider options:
  - **imgui (C++/Python bindings):** High FPS, good for 120hz displays
  - **Web Dashboard (React/Next.js):** Cross-platform, modern UI
  - **Electron + React:** Desktop app with web technologies
  - **Textual (Python):** Modern terminal UI framework

---

## 🟢 Low Priority

### Infrastructure
- [ ] **Remove Unused Dependencies:** `aiohttp` and `loguru` in requirements.txt
- [ ] **Add Development Dependencies:** pytest, mypy, ruff/black
- [ ] **Type Safety:** Use pydantic for config validation
- [ ] **Monitoring/Alerting:** Prometheus metrics, health check endpoint

### Strategies
- [ ] **Cross-Asset Signals:** Crypto/futures price feeds
- [ ] **Sentiment/NLP Strategy:** News API + FinBERT
- [ ] **VPIN/Order Flow:** Blockchain wallet tracking
- [ ] **Manipulation Counter-Trading:** Wash trading detection

### Documentation
- [ ] **Deployment Guide:** VPS setup, systemd service
- [ ] **Troubleshooting Guide:** Common errors and fixes
- [ ] **Architecture Diagrams:** Visual component overview

---

## ✅ Completed

- [x] Core bot architecture
- [x] Modular strategy system
- [x] Risk management (position limits, circuit breakers)
- [x] Paper trading mode
- [x] Rate limiting
- [x] Spread strategy
- [x] Arbitrage strategy (intra-platform)
- [x] Stink bid strategy
- [x] Favorite-longshot bias strategy
- [x] Late money strategy
- [x] Multi-strategy mode (`--strategy all`)
- [x] Graceful shutdown handling

---

## Notes

### CLI Dashboard Options

**Option 1: Rich Library (Recommended)**
```python
from rich.live import Live
from rich.table import Table
from rich.console import Console

# Updates terminal in-place, no scrolling
with Live(generate_table(), refresh_per_second=1) as live:
    while running:
        live.update(generate_table())
```

**Option 2: Curses (Native)**
- Built-in Python library
- Full terminal control
- More complex to implement

**Option 3: Textual (Modern)**
- By the Rich team
- CSS-like styling
- Async-first design

### GUI Framework Comparison

| Framework | Language | Pros | Cons |
|-----------|----------|------|------|
| imgui-python | Python | Fast, 120hz capable | Steep learning curve |
| Dear PyGui | Python | Easy, fast | Less flexible |
| React + Electron | JS | Modern UI, cross-platform | Heavy, requires Node |
| Tauri + React | JS/Rust | Lightweight | Requires Rust toolchain |
| PyQt6/PySide6 | Python | Native look, mature | Complex licensing |
