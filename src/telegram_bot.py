"""Telegram control panel for two isolated paper experiments."""
from __future__ import annotations

import asyncio
import html
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
UNITS = {"ml": "polymarket-ml.service", "spread": "polymarket-spread.service"}
DATABASES = {
    "ml": ROOT / "data" / "ml_15m_paper.sqlite",
    "spread": ROOT / "data" / "spread_15m_paper.sqlite",
}


def systemctl(action: str, unit: str) -> str:
    return subprocess.run(
        ["systemctl", action, unit], text=True, capture_output=True, check=False
    ).stdout.strip()


def active(name: str) -> bool:
    return systemctl("is-active", UNITS[name]) == "active"


def stats(name: str) -> tuple[int, int, int, float]:
    try:
        with sqlite3.connect(DATABASES[name]) as db:
            orders = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            trades = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            positions = db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            pnl = db.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) + COALESCE(SUM(unrealized_pnl), 0) FROM positions"
            ).fetchone()[0]
        return orders, trades, positions, float(pnl or 0)
    except (OSError, sqlite3.Error):
        return 0, 0, 0, 0.0


def scanned_markets(name: str) -> int:
    output = subprocess.run(
        ["journalctl", "-u", UNITS[name], "-n", "200", "--no-pager", "-o", "cat"],
        text=True, capture_output=True, check=False,
    ).stdout
    matches = re.findall(r"(?:Found|Refreshed) (\d+) tradeable markets", output)
    return int(matches[-1]) if matches else 0


def experiment_text(name: str, title: str) -> str:
    orders, trades, positions, pnl = stats(name)
    return (
        f"{title}: {'🟢' if active(name) else '⚪'}\n"
        f"Банк: $100.00 · PnL: ${pnl:+.2f}\n"
        f"Ордера: {orders} · Сделки: {trades} · Позиции: {positions}\n"
        f"Обзор рынков: {scanned_markets(name)}"
    )


def status_text(prefix: str = "") -> str:
    return (
        f"{prefix}📊 BTC 15m PAPER\n\n"
        f"{experiment_text('ml', '🧠 ML maker')}\n\n"
        f"{experiment_text('spread', '📈 A-S maker')}\n\n"
        "BTC 5m: 🔴 выключен\nLive: 🔒 выключен"
    )


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ ML", callback_data="start:ml"), InlineKeyboardButton(text="⏹ ML", callback_data="stop:ml")],
        [InlineKeyboardButton(text="▶️ A-S", callback_data="start:spread"), InlineKeyboardButton(text="⏹ A-S", callback_data="stop:spread")],
        [InlineKeyboardButton(text="▶️ Оба", callback_data="start:all"), InlineKeyboardButton(text="⏹ Оба", callback_data="stop:all")],
        [InlineKeyboardButton(text="📜 ML лог", callback_data="log:ml"), InlineKeyboardButton(text="📜 A-S лог", callback_data="log:spread")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="status")],
    ])


def permitted(event: Message | CallbackQuery) -> bool:
    return bool(CHAT_ID and event.from_user and str(event.from_user.id) == CHAT_ID)


async def edit(call: CallbackQuery, text: str) -> None:
    await call.message.edit_text(text, reply_markup=menu(), parse_mode="HTML")
    await call.answer()


async def run_daily() -> None:
    if not TOKEN or not CHAT_ID:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in /opt/polymarket-bot/.env")
    async with Bot(TOKEN) as bot:
        await bot.send_message(CHAT_ID, status_text("⏰ Ежедневная проверка\n\n"), reply_markup=menu())


async def main() -> None:
    if not TOKEN or not CHAT_ID:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in /opt/polymarket-bot/.env")
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        if not permitted(message):
            return
        await message.answer(status_text(), reply_markup=menu())
        try:
            await message.delete()
        except Exception:
            pass

    @dp.message()
    async def discard(message: Message) -> None:
        if permitted(message):
            try:
                await message.delete()
            except Exception:
                pass

    @dp.callback_query(F.data == "status")
    async def status(call: CallbackQuery) -> None:
        if permitted(call):
            await edit(call, status_text())

    @dp.callback_query(F.data.startswith("start:"))
    async def start_unit(call: CallbackQuery) -> None:
        if not permitted(call):
            return
        target = call.data.split(":", 1)[1]
        names = UNITS if target == "all" else (target,)
        for name in names:
            systemctl("start", UNITS[name])
        await edit(call, status_text("✅ Запуск выполнен\n\n"))

    @dp.callback_query(F.data.startswith("stop:"))
    async def stop_unit(call: CallbackQuery) -> None:
        if not permitted(call):
            return
        target = call.data.split(":", 1)[1]
        names = UNITS if target == "all" else (target,)
        for name in names:
            systemctl("stop", UNITS[name])
        await edit(call, status_text("⏹ Остановка выполнена\n\n"))

    @dp.callback_query(F.data.startswith("log:"))
    async def log(call: CallbackQuery) -> None:
        if not permitted(call):
            return
        name = call.data.split(":", 1)[1]
        output = subprocess.run(
            ["journalctl", "-u", UNITS[name], "-n", "25", "--no-pager", "-o", "cat"],
            text=True, capture_output=True, check=False,
        ).stdout[-3000:] or "Логов пока нет"
        await edit(call, f"📜 {name}\n<pre>{html.escape(output)}</pre>")

    await dp.start_polling(Bot(TOKEN))


if __name__ == "__main__":
    asyncio.run(run_daily() if "--daily" in sys.argv else main())
