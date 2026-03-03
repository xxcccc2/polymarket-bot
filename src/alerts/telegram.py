"""
Telegram Alert System

Sends real-time notifications to a Telegram chat:
- Order fills (with strategy, P&L)
- Risk events (throttle, halt, resume)
- Daily P&L summary
- Errors and warnings
- Strategy health changes (auto-disable/enable)

Setup:
  1. Message @BotFather on Telegram → /newbot → get your BOT_TOKEN
  2. Message your bot, then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your CHAT_ID
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Deque, Dict, Optional

import requests

from ..logging_utils import cprint


class TelegramAlerter:
    """
    Non-blocking Telegram notification sender.

    Messages are queued and sent from a background thread so they
    never block the trading loop.
    """

    # Rate limit: max 30 messages per second (Telegram limit)
    # We'll be conservative: max 1 message per second
    MIN_SEND_INTERVAL = 1.0

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        quiet_hours: bool = False,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token) and bool(chat_id)
        self.quiet_hours = quiet_hours

        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._queue: Deque[str] = deque(maxlen=100)
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_send = 0.0

        # Daily summary accumulator
        self._daily_fills = 0
        self._daily_pnl = 0.0
        self._daily_fees = 0.0
        self._daily_signals = 0
        self._session_start = datetime.now()

        if self.enabled:
            self._start()
            cprint("  📱 Telegram alerts enabled", "cyan")
        else:
            if not bot_token or not chat_id:
                cprint("  📱 Telegram alerts disabled (no token/chat_id)", "yellow")
            else:
                cprint("  📱 Telegram alerts disabled", "yellow")

    # ------------------------------------------------------------------
    # Public API — called from bot/risk_manager/strategies
    # ------------------------------------------------------------------

    def alert_fill(
        self,
        strategy: str,
        side: str,
        price: float,
        size: float,
        market: str,
        pnl: float = 0.0,
    ) -> None:
        """Alert on order fill."""
        self._daily_fills += 1
        self._daily_pnl += pnl

        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        pnl_str = f"  P&L: ${pnl:+.2f}" if pnl != 0 else ""

        self._enqueue(
            f"{emoji} *FILL* [{strategy.upper()}]\n"
            f"{side.upper()} {size:.2f} @ ${price:.3f}\n"
            f"Market: {market[:60]}\n"
            f"{pnl_str}"
        )

    def alert_risk_event(self, level: str, message: str) -> None:
        """Alert on risk level change."""
        icons = {
            "normal": "✅",
            "warning": "⚠️",
            "critical": "🚨",
            "halted": "🛑",
        }
        icon = icons.get(level.lower(), "❓")
        self._enqueue(f"{icon} *RISK {level.upper()}*\n{message}")

    def alert_throttle(self, factor: float, drawdown_pct: float) -> None:
        """Alert when drawdown throttle activates."""
        if factor <= 0:
            self._enqueue(
                f"🛑 *TRADING HALTED*\n"
                f"Drawdown: {drawdown_pct*100:.1f}%\n"
                f"All trading stopped until manual resume."
            )
        else:
            self._enqueue(
                f"⚠️ *THROTTLE ACTIVE*\n"
                f"Drawdown: {drawdown_pct*100:.1f}%\n"
                f"Trade size reduced to {factor*100:.0f}%"
            )

    def alert_strategy_health(self, strategy: str, healthy: bool, reason: str) -> None:
        """Alert when strategy health changes."""
        if not healthy:
            self._enqueue(
                f"⏸️ *Strategy Disabled*: {strategy}\n"
                f"Reason: {reason}"
            )
        else:
            self._enqueue(f"▶️ *Strategy Re-enabled*: {strategy}")

    def alert_error(self, context: str, error: str) -> None:
        """Alert on error."""
        self._enqueue(f"❌ *ERROR* [{context}]\n{error[:200]}")

    def alert_startup(self, balance: float, strategies: list, mode: str) -> None:
        """Alert on bot startup."""
        strat_list = ", ".join(strategies)
        try:
            from ..native.pmkernel import NATIVE_AVAILABLE
            engine = "libpmkernel (native)" if NATIVE_AVAILABLE else "pure-Python fallback"
        except Exception:
            engine = "pure-Python fallback"
        self._enqueue(
            f"🚀 *Bot Started*\n"
            f"Mode: {mode}\n"
            f"Balance: ${balance:.2f}\n"
            f"Strategies: {strat_list}\n"
            f"Engine: {engine}"
        )

    def alert_shutdown(self, reason: str = "User requested") -> None:
        """Alert on bot shutdown with daily summary."""
        runtime = datetime.now() - self._session_start
        hours = runtime.total_seconds() / 3600

        pnl_emoji = "📈" if self._daily_pnl >= 0 else "📉"

        self._enqueue(
            f"🛑 *Bot Stopped*\n"
            f"Reason: {reason}\n"
            f"Runtime: {hours:.1f}h\n\n"
            f"{pnl_emoji} *Session Summary*\n"
            f"Fills: {self._daily_fills}\n"
            f"Net P&L: ${self._daily_pnl:+.2f}\n"
            f"Fees: ${self._daily_fees:.2f}"
        )
        # Give the queue time to flush
        time.sleep(2)

    def alert_greeks(self, net_delta: float, net_gamma: float) -> None:
        """Alert when portfolio Greeks exceed thresholds."""
        parts = []
        if abs(net_delta) > 0.3:
            parts.append(f"Delta: {net_delta:+.4f}")
        if abs(net_gamma) > 0.2:
            parts.append(f"Gamma: {net_gamma:+.4f}")
        if parts:
            self._enqueue(
                f"⚠️ *Portfolio Greeks Alert*\n"
                + "\n".join(parts)
                + "\nConsider rebalancing."
            )

    def alert_shock_rejection(self, token_id: str, worst_pnl: float, reason: str) -> None:
        """Alert when a trade is rejected by the shock test."""
        self._enqueue(
            f"🛡️ *Shock Test Rejected*\n"
            f"Token: {token_id[:16]}…\n"
            f"Worst PnL: ${worst_pnl:.2f}\n"
            f"{reason}"
        )

    def send_daily_summary(self, risk_status: Dict) -> None:
        """Send end-of-day summary."""
        balance = risk_status.get("current_balance", 0)
        daily_pnl = risk_status.get("daily_pnl", 0)
        trades = risk_status.get("daily_trades", 0)
        exposure = risk_status.get("total_exposure", 0)
        throttle = risk_status.get("throttle_factor", 1.0)
        net_delta = risk_status.get("net_delta", 0)
        net_gamma = risk_status.get("net_gamma", 0)

        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"

        self._enqueue(
            f"📊 *Daily Summary*\n"
            f"Balance: ${balance:.2f}\n"
            f"{pnl_emoji} Daily P&L: ${daily_pnl:+.2f}\n"
            f"Trades: {trades}\n"
            f"Exposure: ${exposure:.2f}\n"
            f"Throttle: {throttle*100:.0f}%\n"
            f"Delta: {net_delta:+.4f}  Gamma: {net_gamma:+.4f}"
        )

    # ------------------------------------------------------------------
    # Internal — queue + background sender
    # ------------------------------------------------------------------

    def _enqueue(self, text: str) -> None:
        """Add message to send queue."""
        if not self.enabled:
            return
        with self._lock:
            self._queue.append(text)

    def _start(self) -> None:
        """Start background sender thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background sender (flushes remaining messages)."""
        self._running = False
        # Flush remaining
        while self._queue:
            self._send_next()
        if self._thread:
            self._thread.join(timeout=5)

    def _send_loop(self) -> None:
        """Background loop that drains the queue."""
        while self._running:
            if self._queue:
                self._send_next()
            time.sleep(0.5)

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Escape characters that break Telegram MarkdownV1 outside bold/italic."""
        # Bold (*...*) markers are intentional — leave those alone.
        # But underscores in words like cross_asset break the parser.
        # Replace _ with \_ ONLY outside *...* blocks.
        parts = text.split("*")
        for i in range(len(parts)):
            if i % 2 == 0:  # outside bold markers
                parts[i] = parts[i].replace("_", "\\_")
        return "*".join(parts)

    def _send_next(self) -> None:
        """Send the next message in the queue."""
        with self._lock:
            if not self._queue:
                return
            text = self._queue.popleft()

        # Rate limit
        elapsed = time.time() - self._last_send
        if elapsed < self.MIN_SEND_INTERVAL:
            time.sleep(self.MIN_SEND_INTERVAL - elapsed)

        safe_text = self._escape_markdown(text)

        try:
            resp = requests.post(
                self._api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": safe_text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            self._last_send = time.time()

            if resp.status_code != 200:
                cprint(f"  📱 Telegram send failed ({resp.status_code}): {resp.text[:120]}", "yellow")

        except Exception as e:
            cprint(f"  📱 Telegram error: {e}", "yellow")
