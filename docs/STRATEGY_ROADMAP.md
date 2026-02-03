# Polymarket Bot - Strategy Roadmap

**Last Updated:** February 2025  
**Status:** Active Development

---

## Implementation Status Overview

| Strategy | Status | Priority | Complexity | Est. Edge |
|----------|--------|----------|------------|-----------|
| Spread/Market Making | ✅ Implemented | - | Low | ~20% per trade |
| Intra-Platform Arbitrage | ✅ Implemented | - | Low | 2-5% risk-free |
| Stink Bid | ✅ Implemented | - | Low | 100x potential |
| Favorite-Longshot Bias | ✅ Implemented | - | Low | 10-20% |
| Late Money | ✅ Implemented | - | Medium | 3-8% |
| **Cross-Platform Arbitrage** | 🔴 Not Started | **HIGH** | Medium | 2-5% risk-free |
| Anchoring Bias | 🔴 Not Started | Medium | Low | 5-10% |
| Overreaction/Mean-Reversion | 🔴 Not Started | Medium | Medium | 2-5% |
| Time Decay | 🔴 Not Started | Medium | Low | 5-15% |
| Combinatorial Arbitrage | 🔴 Not Started | High | High | $40M+ extracted |
| Cross-Asset Signals | 🔴 Not Started | Medium | High | 5-10% |
| Sentiment/NLP | 🔴 Not Started | Low | High | 55%+ accuracy |
| VPIN/Order Flow | 🔴 Not Started | Low | Very High | Variable |
| Manipulation Counter-Trading | 🔴 Not Started | Low | High | Variable |
| Liquidity Provision (Rewards) | 🔴 Not Started | Medium | Medium | $50/market |
| Hedge Strategy (PerpDEX) | 🔴 Not Started | Low | Very High | Variable |

---

## ✅ Implemented Strategies (5)

### 1. Spread Strategy (`spread_strategy.py`)
**Status:** Production Ready  
**Description:** Micro-spread farming - profits from bid-ask spreads

- Identifies markets with wide spreads (> 2 cents)
- Places limit buys at bid price
- Places limit sells at bid + target spread
- Captures spread minus fees (~20% per trade)
- Cooldown mechanism prevents over-trading

**Based on:** Market Making / Avellaneda-Stoikov model concepts

---

### 2. Arbitrage Strategy (`arbitrage_strategy.py`)
**Status:** Production Ready  
**Description:** Intra-platform arbitrage when YES + NO < $1

- Monitors combined YES/NO prices
- When total < 100¢, buy both outcomes
- Guaranteed profit on resolution (risk-free)
- Works on multi-outcome markets

**Academic Basis:** Fundamental invariant exploitation (Saguillo et al. 2025)

---

### 3. Stink Bid Strategy (`stink_bid_strategy.py`)
**Status:** Production Ready  
**Description:** Low-risk asymmetric bets for 100x potential

- Find markets with high volume but thin orderbooks
- Place 1¢ limit bids waiting for orderbook "nukes"
- Captures panic sells and fat-finger errors
- Perfect for small accounts

**Academic Basis:** Documented in prediction market literature as "stink bidding"

---

### 4. Favorite-Longshot Bias Strategy (`favorite_longshot_strategy.py`)
**Status:** Production Ready  
**Description:** Exploits the most documented prediction market inefficiency

- **Fade longshots:** Sell/avoid contracts priced 2-10¢ (overpriced)
- **Buy favorites:** Buy contracts priced 85-95¢ (underpriced)
- Filter by time-to-expiration (prefer 3+ months out)

**Academic Basis:** 
- Snowberg & Wolfers (2010): 55+ percentage point edge
- Page & Clemen (2013): Political markets especially affected
- Green et al. (2024): Bias strengthens late in trading periods

---

### 5. Late Money Strategy (`late_money_strategy.py`)
**Status:** Production Ready  
**Description:** Follow informed traders near expiration

- Monitor price velocity in final hours before resolution
- 40% of volume occurs in last minute (more informed)
- Follow sharp late moves rather than fade them

**Academic Basis:**
- Gramm & McKinney (2009): Late money is systematically more informed
- Asch, Malkiel & Quandt (1982): "Crafts Ratio" predicts returns
- 3-8% improved predictive accuracy over early prices

---

## 🔴 Roadmap - High Priority

### 6. Cross-Platform Arbitrage Strategy ⭐ PRIORITY
**Complexity:** Medium  
**Dependencies:** Kalshi API integration  
**Estimated Edge:** 2-5% risk-free per trade

**Description:** Exploit price discrepancies between Polymarket and Kalshi

**Implementation:**
```
1. Integrate Kalshi API (predmarket SDK or custom)
2. Match identical markets across platforms
3. Monitor combined prices (YES_poly + NO_kalshi)
4. Execute when total < $1 (accounting for fees on both sides)
5. Handle settlement timing differences
```

**Risks:**
- Legging risk (one side fills, other doesn't)
- Different settlement timing
- Capital locked on two platforms
- Regulatory differences (Kalshi is CFTC-regulated)

**Your Twitter Example:**
> "NBA, Warriors x Wolves... 69c Wolves on Polymarket + 29c Warriors on Kalshi = 98c total = 2% profit"

---

### 7. Combinatorial Arbitrage Strategy ⭐ PRIORITY
**Complexity:** High  
**Dependencies:** NLP/embeddings for market matching  
**Estimated Edge:** Large ($40M extracted from Polymarket per Saguillo et al.)

**Description:** Cross-market logical inconsistencies

**Implementation:**
```
1. Parse market questions for logical dependencies
2. Use LLM/embeddings (Linq-Embed-Mistral) to find related markets
3. Identify logical implications ("BTC >$100k" implies "BTC >$90k")
4. Find pricing violations across related markets
5. Execute multi-leg arbitrage
```

**Academic Basis:** Saguillo et al. (2025) - arXiv:2508.03474

---

### 8. Anchoring Bias Strategy
**Complexity:** Low  
**Dependencies:** Price history tracking  
**Estimated Edge:** 5-10%

**Description:** Exploit price stickiness around psychological levels

**Implementation:**
```
1. Detect prices clustering at 25/50/75¢ round numbers
2. Monitor news events that should move prices
3. Trade away from anchors when fundamentals diverge
4. Track under-adjustment after significant news
```

**Academic Basis:** Tversky & Kahneman (1974), CW Data Solutions (2024)

---

### 9. Overreaction/Mean-Reversion Strategy
**Complexity:** Medium  
**Dependencies:** Price velocity tracking, news detection  
**Estimated Edge:** 2-5% abnormal returns

**Description:** Mean-reversion after extreme moves

**Implementation:**
```
1. Track price movements and velocity
2. Identify extreme events (>20% move)
3. After extreme events, fade the initial reaction
4. Expect 20-40% reversal within 24-48 hours
5. Higher volume = stronger overreaction signal
```

**Academic Basis:**
- Hong & Stein (1999): Overreaction theory
- Barberis, Shleifer & Vishny (1998): Diagnostic expectations
- 2023 Management Science study: Confirmed in betting markets

---

### 10. Time Decay Strategy
**Complexity:** Low  
**Dependencies:** Expiration date tracking  
**Estimated Edge:** 5-15%

**Description:** Long-dated contracts compress toward 50%

**Implementation:**
```
1. Identify markets with 3+ month expiration
2. Buy high-probability outcomes (>70%)
3. Sell low-probability outcomes (<30%)
4. Close positions as expiration approaches
5. Exploits capital lockup discount rate bias
```

**Academic Basis:** Page & Clemen (2013) - calibration analysis

---

## 🟡 Roadmap - Medium Priority

### 11. Cross-Asset Signals Strategy
**Complexity:** High  
**Dependencies:** External price feeds (crypto, futures)

**Description:** Financial markets lead prediction markets

**Implementation:**
```
1. Connect to crypto price feeds (BTC/ETH spot from Binance/etc)
2. Monitor Fed futures for Fed decision markets
3. Trade Polymarket when prediction lags spot price moves
4. Speed edge: traditional markets have HFT, prediction markets don't
```

**Academic Basis:** Snowberg, Wolfers & Zitzewitz (2007, 2011)

---

### 12. Liquidity Provision Strategy (Rewards Farming)
**Complexity:** Medium  
**Dependencies:** Q-score understanding

**Description:** Earn Polymarket liquidity rewards

**Implementation:**
```
1. Identify markets with liquidity incentives
2. Place orders to maximize Q-score
3. Manage inventory risk (Avellaneda-Stoikov model)
4. Earn up to $50 per market in rewards
```

---

### 13. Sentiment/NLP Strategy
**Complexity:** High  
**Dependencies:** News API, NLP models

**Description:** News-based alpha with 5-minute optimal lag

**Implementation:**
```
1. Real-time news pipeline (NewsAPI, CryptoPanic)
2. VADER/FinBERT sentiment scoring
3. Trade divergences between sentiment and price
4. Weekend/holiday sentiment more valuable (lower participation)
```

**Academic Basis:** Bollen et al. (2011) - 55%+ accuracy

---

## 🔵 Roadmap - Advanced/Long-Term

### 14. VPIN/Order Flow Strategy
**Complexity:** Very High  
**Dependencies:** Blockchain indexing, wallet tracking

**Description:** Track informed traders by wallet address

**Implementation:**
```
1. On-chain analysis of trader positions
2. Identify accounts with historically accurate predictions
3. Follow their position directions
4. During high-VPIN periods, trade with informed flow
```

**Academic Basis:** Easley, López de Prado & O'Hara (2012)

---

### 15. Manipulation Counter-Trading
**Complexity:** High  
**Dependencies:** Wash trading detection

**Description:** Detect and counter wash trading/manipulation

- 25% of Polymarket volume is wash trading
- Identify unusual price moves without news
- Counter-trade manipulation attempts
- Markets revert after manipulation

**Academic Basis:** Itzhak, Rasooly & Rozzi (2025), Columbia University (2025)

---

### 16. Hedge Strategy (PerpDEX)
**Complexity:** Very High  
**Dependencies:** Perpdex/HyperLiquid integration

**Description:** Cross-platform hedging

- Short on Polymarket prediction
- Hedge with long on perpetual DEX
- Capture spread between funding rates

---

## Kelly Criterion Implementation

All strategies should implement Kelly-based position sizing:

```python
def kelly_fraction(p: float, b: float) -> float:
    """
    Calculate optimal Kelly bet fraction.
    
    Args:
        p: Probability of winning (your estimate)
        b: Net odds (payout/cost - 1)
    
    Returns:
        Optimal fraction of bankroll to bet
    """
    q = 1 - p
    f_star = (b * p - q) / b
    
    # Use fractional Kelly (half or quarter) to reduce volatility
    return max(0, f_star * 0.5)  # Half-Kelly
```

---

## Infrastructure Requirements

| Capability | Status | Priority |
|------------|--------|----------|
| REST API polling | ✅ Working | - |
| WebSocket feed | ⚠️ Disabled | Medium |
| Persistence (SQLite) | ❌ Missing | **High** |
| Unit tests | ❌ Missing | **High** |
| Proper logging | ❌ Missing | Medium |
| Balance tracking | ⚠️ Incomplete | High |
| Cross-platform (Kalshi) | ❌ Missing | **High** |
| News/sentiment feed | ❌ Missing | Medium |
| Blockchain indexing | ❌ Missing | Low |

---

## Next Development Priorities

### Phase 1: Foundation (Current)
1. ✅ Core strategies implemented (5/5)
2. 🔄 Fix balance tracking in `client.py`
3. 🔄 Add SQLite persistence for orders/positions
4. 🔄 Add unit tests for core components
5. 🔄 Implement proper logging (replace termcolor)

### Phase 2: Cross-Platform
1. Integrate Kalshi API
2. Build cross-platform arbitrage strategy
3. Handle multi-platform risk management

### Phase 3: Advanced Strategies
1. Implement combinatorial arbitrage (NLP-based)
2. Add anchoring/overreaction strategies
3. Cross-asset signal integration

### Phase 4: UI/Dashboard
1. Static CLI dashboard (no infinite loops)
2. Optional GUI (imgui or web-based)

---

## References

- Snowberg & Wolfers (2010) - Favorite-longshot bias
- Page & Clemen (2013) - Calibration in prediction markets
- Saguillo et al. (2025) - $40M combinatorial arbitrage on Polymarket
- Easley, López de Prado & O'Hara (2012) - VPIN model
- Hong & Stein (1999) - Overreaction theory
- Gramm & McKinney (2009) - Late money signals
- Tversky & Kahneman (1974) - Anchoring bias
- Bollen et al. (2011) - Sentiment prediction
