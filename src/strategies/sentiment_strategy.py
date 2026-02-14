"""
Sentiment-Driven Trading Strategy

Trades divergences between news sentiment and market prices.

Core thesis: News moves crypto prediction markets with a 2-10 minute lag.
If sentiment turns bullish but the market hasn't repriced, buy before it does.

Data sources:
    - CryptoPanic API (free tier: 5 req/min, crypto-focused news aggregator)
    - Fallback: monitors price velocity as a proxy for "news happened"

How it works:
    1. Poll CryptoPanic for recent crypto news every 60s
    2. Score each headline with keyword-based sentiment (-1.0 to +1.0)
    3. Match news to relevant Polymarket markets by keyword overlap
    4. If sentiment diverges from current price → generate signal
    5. Trade the divergence before the market catches up

References:
    - Bollen et al. (2011): Twitter mood predicts stock moves (55%+ accuracy)
    - Tetlock (2007): Media sentiment predicts market returns
    - 5-minute optimal lag for news → prediction market repricing
"""

from __future__ import annotations

import time
import re
import threading
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

import requests

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..config import (
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
)


# ----- Sentiment Configuration Defaults -----

# CryptoPanic API (free tier: no auth needed for public feed)
CRYPTOPANIC_BASE_URL = "https://cryptopanic.com/api/free/v1/posts/"

# How often to poll news (seconds)
SENTIMENT_POLL_INTERVAL = 60

# Sentiment score threshold to act on (-1.0 to 1.0)
SENTIMENT_THRESHOLD = 0.40

# Minimum price divergence between sentiment and market to trade
SENTIMENT_MIN_DIVERGENCE = 0.05

# Cooldown per market after a signal (seconds)
SENTIMENT_COOLDOWN = 300

# Max news age to consider (seconds)
SENTIMENT_MAX_NEWS_AGE = 600  # 10 minutes

# Minimum confidence
SENTIMENT_MIN_CONFIDENCE = 0.55

# Maximum buy price
SENTIMENT_MAX_BUY_PRICE = 0.90
SENTIMENT_MIN_BUY_PRICE = 0.05


# ----- Keyword Sentiment Lexicon -----
# Scores from -1.0 (very bearish) to +1.0 (very bullish)

BULLISH_KEYWORDS = {
    "surge": 0.7, "soar": 0.8, "rally": 0.7, "pump": 0.6,
    "breakout": 0.7, "all-time high": 0.9, "ath": 0.8,
    "bullish": 0.7, "moon": 0.5, "adoption": 0.6,
    "approved": 0.8, "approval": 0.7, "etf": 0.5,
    "partnership": 0.5, "launch": 0.4, "upgrade": 0.5,
    "halving": 0.6, "institutional": 0.5, "accumulate": 0.6,
    "buy": 0.3, "support": 0.3, "recovery": 0.5,
    "record": 0.6, "milestone": 0.5, "growth": 0.4,
    "win": 0.5, "wins": 0.5, "passes": 0.4, "passed": 0.4,
    "higher": 0.3, "rises": 0.4, "gain": 0.4, "gains": 0.4,
    "positive": 0.3, "boost": 0.4, "strong": 0.3,
}

BEARISH_KEYWORDS = {
    "crash": -0.8, "dump": -0.7, "plunge": -0.8, "tank": -0.7,
    "bearish": -0.7, "sell-off": -0.7, "selloff": -0.7,
    "hack": -0.9, "exploit": -0.8, "vulnerability": -0.7,
    "ban": -0.8, "banned": -0.8, "regulation": -0.4,
    "lawsuit": -0.6, "sued": -0.6, "fraud": -0.8,
    "scam": -0.9, "rug": -0.9, "collapse": -0.9,
    "bankrupt": -0.9, "insolvent": -0.9, "default": -0.7,
    "fud": -0.3, "fear": -0.4, "panic": -0.6,
    "lower": -0.3, "drops": -0.4, "drop": -0.4, "falls": -0.4,
    "decline": -0.5, "loss": -0.4, "losses": -0.4,
    "reject": -0.5, "rejected": -0.5, "fails": -0.5,
    "negative": -0.3, "weak": -0.3, "concern": -0.3,
}

# Crypto asset keywords for matching news to markets
CRYPTO_ASSETS = {
    "bitcoin": ["bitcoin", "btc"],
    "ethereum": ["ethereum", "eth"],
    "solana": ["solana", "sol"],
    "xrp": ["xrp", "ripple"],
    "dogecoin": ["doge", "dogecoin"],
    "cardano": ["cardano", "ada"],
    "polygon": ["polygon", "matic"],
    "avalanche": ["avalanche", "avax"],
    "chainlink": ["chainlink", "link"],
    "litecoin": ["litecoin", "ltc"],
}


class NewsItem:
    """A scored news item."""
    __slots__ = ("title", "url", "source", "timestamp", "sentiment", "matched_assets")

    def __init__(self, title: str, url: str, source: str,
                 timestamp: float, sentiment: float,
                 matched_assets: List[str]) -> None:
        self.title = title
        self.url = url
        self.source = source
        self.timestamp = timestamp
        self.sentiment = sentiment
        self.matched_assets = matched_assets

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def __repr__(self) -> str:
        return f"News({self.sentiment:+.2f} | {self.title[:50]})"


class SentimentStrategy(BaseStrategy):
    """
    News sentiment-driven trading.

    Polls CryptoPanic for headlines, scores them, and trades when
    sentiment diverges from current Polymarket prices.

    Config options:
        - cryptopanic_api_key: Optional API key for premium feed
        - sentiment_threshold: Min absolute sentiment to act (default: 0.40)
        - sentiment_poll_interval: Seconds between polls (default: 60)
        - sentiment_cooldown: Cooldown per market (default: 300)
        - order_size_usd: Trade size (default: from config)
    """

    name = "sentiment"
    description = "Trade news sentiment divergences vs market price"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)

        self.api_key = self.config.get("cryptopanic_api_key", "")
        self.threshold = float(self.config.get("sentiment_threshold", SENTIMENT_THRESHOLD))
        self.poll_interval = int(self.config.get("sentiment_poll_interval", SENTIMENT_POLL_INTERVAL))
        self.cooldown = float(self.config.get("sentiment_cooldown", SENTIMENT_COOLDOWN))
        self.min_divergence = float(self.config.get("sentiment_min_divergence", SENTIMENT_MIN_DIVERGENCE))
        self.min_confidence = float(self.config.get("sentiment_min_confidence", SENTIMENT_MIN_CONFIDENCE))
        self.order_size = float(self.config.get("order_size_usd", ORDER_SIZE_USD))

        # Recent news buffer
        self._news: deque[NewsItem] = deque(maxlen=200)
        self._last_poll = 0.0
        self._poll_lock = threading.Lock()

        # Cooldowns
        self._last_signal_time: Dict[str, float] = {}

        # Aggregate sentiment per asset (rolling)
        self._asset_sentiment: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

        cprint(f"   📰 Sentiment: threshold={self.threshold}, "
               f"poll={self.poll_interval}s, "
               f"api={'key set' if self.api_key else 'public feed'}",
               "white")

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """Poll news, score sentiment, find divergences."""
        signals: List[Signal] = []
        now = time.time()

        # Poll news if due
        if now - self._last_poll >= self.poll_interval:
            self._poll_news()
            self._last_poll = now

        # Get current sentiment per asset
        asset_scores = self._get_aggregate_sentiment(now)

        if not asset_scores:
            return signals

        for md in market_data:
            token_id = md.token_id
            if not token_id:
                continue

            slug = (md.market_slug or "").lower()
            question = (md.question or slug).lower()

            # Match market to a crypto asset
            matched_asset = None
            for asset_name, keywords in CRYPTO_ASSETS.items():
                if any(kw in question or kw in slug for kw in keywords):
                    matched_asset = asset_name
                    break

            if not matched_asset or matched_asset not in asset_scores:
                continue

            sentiment = asset_scores[matched_asset]

            # Skip weak sentiment
            if abs(sentiment) < self.threshold:
                continue

            # Get market price
            price = md.best_bid if md.best_bid else (md.last_price or 0)
            if price <= 0 or price >= 1:
                continue

            # Determine expected direction
            # Bullish sentiment → price should be higher (for YES tokens)
            # Check if market is about the asset going UP
            is_up_market = any(w in question for w in ["above", "over", "higher", "rise",
                                                        "up", "exceed", "reach", ">"])
            is_down_market = any(w in question for w in ["below", "under", "lower", "fall",
                                                          "down", "drop", "<"])

            if is_up_market:
                # Bullish sentiment + low price = buy YES
                if sentiment > 0 and price < (0.5 + sentiment * 0.3):
                    divergence = sentiment * 0.5 - (price - 0.5)
                else:
                    continue
            elif is_down_market:
                # Bullish sentiment + high YES price on "down" market = sell/skip
                # Bearish sentiment + low YES price on "down" market = buy
                if sentiment < 0 and price < (0.5 + abs(sentiment) * 0.3):
                    divergence = abs(sentiment) * 0.5 - (price - 0.5)
                else:
                    continue
            else:
                # Generic market — just use sentiment direction
                if sentiment > 0 and price < 0.5:
                    divergence = sentiment - price
                elif sentiment < 0 and price > 0.5:
                    divergence = abs(sentiment) - (1.0 - price)
                else:
                    continue

            if divergence < self.min_divergence:
                continue

            # Cooldown
            last = self._last_signal_time.get(token_id, 0)
            if now - last < self.cooldown:
                continue

            # Price bounds
            if price > SENTIMENT_MAX_BUY_PRICE or price < SENTIMENT_MIN_BUY_PRICE:
                continue

            confidence = min(0.90, 0.5 + divergence * 0.8)
            if confidence < self.min_confidence:
                continue

            signal = Signal(
                strategy=self.name,
                signal_type=SignalType.BUY,
                token_id=token_id,
                market_slug=md.market_slug or "",
                price=price,
                size=self.order_size,
                confidence=confidence,
                metadata={
                    "sentiment": round(sentiment, 3),
                    "divergence": round(divergence, 3),
                    "asset": matched_asset,
                    "market_type": "up" if is_up_market else ("down" if is_down_market else "generic"),
                },
            )
            signals.append(signal)
            self._last_signal_time[token_id] = now

            cprint(
                f"   📰 Sentiment signal: BUY {md.market_slug[:40]} | "
                f"sent={sentiment:+.2f} price=${price:.3f} div={divergence:.2f}",
                "magenta",
            )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute sentiment signals."""
        results: List[Dict] = []

        for sig in signals:
            if sig.price <= 0 or sig.price >= 1 or sig.size <= 0:
                continue

            result = order_manager.place_limit_order(
                token_id=sig.token_id,
                side="BUY",
                price=sig.price,
                size=sig.size,
                market_slug=sig.market_slug,
                metadata={
                    "strategy": self.name,
                    "sentiment": sig.metadata.get("sentiment"),
                    "asset": sig.metadata.get("asset"),
                    "confidence": sig.confidence,
                    "entry_price": sig.price,
                },
            )
            results.append(result)

            if result.get("success"):
                self.trades_executed += 1

        return results

    # ------------------------------------------------------------------
    # News polling & scoring
    # ------------------------------------------------------------------

    def _poll_news(self) -> None:
        """Fetch recent news from CryptoPanic."""
        with self._poll_lock:
            try:
                params = {"filter": "rising", "public": "true"}
                if self.api_key:
                    params["auth_token"] = self.api_key

                resp = requests.get(
                    CRYPTOPANIC_BASE_URL,
                    params=params,
                    timeout=10,
                )

                if resp.status_code != 200:
                    return

                data = resp.json()
                results = data.get("results", [])

                for item in results:
                    title = item.get("title", "")
                    url = item.get("url", "")
                    source = item.get("source", {}).get("title", "unknown")

                    # Parse timestamp
                    published = item.get("published_at", "")
                    try:
                        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        ts = dt.timestamp()
                    except (ValueError, AttributeError):
                        ts = time.time()

                    # Score sentiment
                    sentiment = self._score_headline(title)

                    # Match to crypto assets
                    matched = self._match_assets(title)

                    news = NewsItem(
                        title=title,
                        url=url,
                        source=source,
                        timestamp=ts,
                        sentiment=sentiment,
                        matched_assets=matched,
                    )
                    self._news.append(news)

                    # Update per-asset sentiment
                    for asset in matched:
                        self._asset_sentiment[asset].append((ts, sentiment))

            except Exception as e:
                # News polling is best-effort
                cprint(f"   📰 News poll error: {e}", "yellow")

    def _score_headline(self, title: str) -> float:
        """Score a headline using keyword sentiment lexicon."""
        title_lower = title.lower()
        scores: List[float] = []

        for word, score in BULLISH_KEYWORDS.items():
            if word in title_lower:
                scores.append(score)

        for word, score in BEARISH_KEYWORDS.items():
            if word in title_lower:
                scores.append(score)

        if not scores:
            return 0.0

        # Weighted average, capped at [-1, 1]
        avg = sum(scores) / len(scores)
        return max(-1.0, min(1.0, avg))

    def _match_assets(self, title: str) -> List[str]:
        """Match headline to crypto assets."""
        title_lower = title.lower()
        matched = []
        for asset_name, keywords in CRYPTO_ASSETS.items():
            if any(kw in title_lower for kw in keywords):
                matched.append(asset_name)
        return matched

    def _get_aggregate_sentiment(self, now: float) -> Dict[str, float]:
        """Get aggregate sentiment per asset from recent news."""
        result: Dict[str, float] = {}
        max_age = SENTIMENT_MAX_NEWS_AGE

        for asset, entries in self._asset_sentiment.items():
            recent = [(ts, score) for ts, score in entries if now - ts < max_age]
            if not recent:
                continue

            # Time-weighted average (newer news counts more)
            total_weight = 0.0
            weighted_sum = 0.0
            for ts, score in recent:
                age = now - ts
                weight = max(0.1, 1.0 - age / max_age)
                weighted_sum += score * weight
                total_weight += weight

            if total_weight > 0:
                result[asset] = weighted_sum / total_weight

        return result

    def get_state(self) -> Dict:
        """Return strategy state."""
        state = super().get_state()
        state["news_buffer_size"] = len(self._news)
        state["tracked_assets"] = len(self._asset_sentiment)
        return state
