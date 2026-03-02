# Reverse Engineering: Strategy Playbook

*Generated from `scripts/reverse_engineer_wallets.py`*

---

## 0x1979ae6b...637c9d

### 📊 PRICE DISTRIBUTION (where they buy)
| Bucket   | Count | %    |
|----------|-------|------|
| 0-5¢     | 71    | 14%  |
| 5-20¢    | 57    | 12%  |
| 20-50¢   | 136   | 28%  |
| 50-80¢   | 178   | 36%  |
| 80-100¢  | 49    | 10%  |

### 💰 SIZE DISTRIBUTION
| Bucket   | Count | %    |
|----------|-------|------|
| <$2      | 203   | 41%  |
| $2-5     | 71    | 14%  |
| $5-15    | 91    | 19%  |
| $15-50   | 83    | 17%  |
| $50+     | 43    | 9%   |

**Avg buy:** $14.22

### 📈 OUTCOME PREFERENCE (UP vs DOWN in up/down markets)
- UP: 491

### ⏱ HORIZON MIX
- 5min: 278
- 15min: 213

### 🔄 SELL RATIO
1.8% (low = hold to resolution)

### 🎯 CHEAP BUYS (<10¢) — sample
| Price | USD   | Outcome | Market |
|-------|-------|---------|--------|
| 8.0¢  | $7.68 | UP      | btc-updown-5m-1772382900... |
| 1.0¢  | $0.96 | UP      | btc-updown-15m-1772381700... |
| 1.0¢  | $0.96 | UP      | btc-updown-15m-1772381700... |
| 1.0¢  | $0.96 | UP      | btc-updown-15m-1772380800... |
| 1.0¢  | $0.96 | UP      | btc-updown-5m-1772380800... |
| 1.0¢  | $0.15 | UP      | btc-updown-5m-1772380800... |
| 1.0¢  | $0.05 | UP      | btc-updown-5m-1772380800... |
| 1.0¢  | $0.02 | UP      | btc-updown-5m-1772380800... |

### 📋 INFERRED PLAYBOOK
- Buys cheap (<20¢) often (26%) — lottery / mispricing plays
- Buys expensive (50¢+) often (46%) — near-certainty / terminal
- Holds to resolution (rarely sells)
- Outcome bias: UP (UP=491, DOWN=0)
- Focus: BTC 5/15-min up/down markets

---

## 0x1d003413...8b0313 (Canine-Commandment)

### 📊 PRICE DISTRIBUTION (where they buy)
| Bucket   | Count | %    |
|----------|-------|------|
| 0-5¢     | 19    | 4%   |
| 5-20¢    | 45    | 9%   |
| 20-50¢   | 199   | 42%  |
| 50-80¢   | 121   | 25%  |
| 80-100¢  | 94    | 20%  |

### 💰 SIZE DISTRIBUTION
| Bucket   | Count | %    |
|----------|-------|------|
| <$2      | 135   | 28%  |
| $2-5     | 93    | 19%  |
| $5-15    | 111   | 23%  |
| $15-50   | 73    | 15%  |
| $50+     | 66    | 14%  |

**Avg buy:** $37.34

### 📈 OUTCOME PREFERENCE (UP vs DOWN in up/down markets)
- UP: 478

### ⏱ HORIZON MIX
- 1h: 267
- 5min: 203
- 15min: 8

### 🔄 SELL RATIO
4.6% (low = hold to resolution)

### 🎯 CHEAP BUYS (<10¢) — sample
| Price | USD   | Outcome | Market |
|-------|-------|---------|--------|
| 8.6¢  | $7.33 | UP      | btc-updown-4h-1772370000... |
| 1.7¢  | $0.34 | UP      | sol-updown-4h-1772370000... |
| 1.5¢  | $0.15 | UP      | sol-updown-4h-1772370000... |
| 2.2¢  | $0.22 | UP      | sol-updown-4h-1772370000... |
| 2.2¢  | $0.33 | UP      | sol-updown-4h-1772370000... |
| 3.0¢  | $0.60 | UP      | xrp-up-or-down-march-1-11am-et... |
| 3.0¢  | $0.52 | UP      | xrp-up-or-down-march-1-11am-et... |
| 2.2¢  | $0.44 | UP      | sol-updown-4h-1772370000... |

### 📋 INFERRED PLAYBOOK
- Favors 20-50¢ (42%) — balanced risk/reward
- Buys expensive (50¢+) often (45%) — near-certainty / terminal
- Holds to resolution (rarely sells)
- Outcome bias: UP (UP=478, DOWN=0)

---

## 0x1461cc6e...d122d8 (Threshold / Combo-Arb)

*[$825K positions, $111K biggest win](https://polymarket.com)*

### 📊 PRICE DISTRIBUTION (where they buy)
| Bucket   | Count | %    |
|----------|-------|------|
| 0-5¢     | 83    | 17%  |
| 5-20¢    | 16    | 3%   |
| 20-50¢   | 95    | 19%  |
| 50-80¢   | 91    | 18%  |
| 80-100¢  | 215   | 43%  |

### 💰 SIZE DISTRIBUTION
| Bucket   | Count | %    |
|----------|-------|------|
| <$2      | 111   | 22%  |
| $2-5     | 37    | 7%   |
| $5-15    | 56    | 11%  |
| $15-50   | 74    | 15%  |
| $50+     | 222   | 44%  |

**Avg buy:** $182.62

### 📈 OUTCOME PREFERENCE
- NO: 292 (threshold markets)
- YES: 142 (threshold markets)
- UP: 66 (up/down markets)

### ⏱ HORIZON MIX
- 1h: 500 (all)

### 🔄 SELL RATIO
0.0% (hold to resolution)

### 🎯 CHEAP BUYS (<10¢) — sample
| Price | USD    | Outcome | Market |
|-------|--------|---------|--------|
| 2.2¢  | $109.97 | YES    | will-bitcoin-reach-70k-february-23-march-1... |
| 2.4¢  | $66.00  | YES    | will-bitcoin-reach-70k-february-23-march-1... |
| 2.4¢  | $3.14   | YES    | will-bitcoin-reach-70k-february-23-march-1... |
| 2.5¢  | $0.12   | YES    | will-the-price-of-bitcoin-be-between-68000-70... |

### 📋 INFERRED PLAYBOOK
- Threshold / combo-arb style ("Will BTC reach 70k?", "Will BTC be between 68k–70k?")
- Buys both YES and NO — exploits monotonicity violations
- 1h only, no 5min scalp
- Large size ($182 avg) — high conviction
- Buys expensive (50¢+) often (61%) — near-certainty
- Aligns with `combinatorial_arb` strategy

---

## Compare

| Metric | 0x1979ae6b | 0x1d003413 | 0x1461cc6e |
|--------|------------|------------|------------|
| Focus | 5/15min up/down | 5min + 1h up/down | 1h threshold |
| Avg size | $14 | $37 | $182 |
| Cheap (<20¢) | 26% | 13% | 20% |
| Expensive (50¢+) | 46% | 45% | 61% |
| Sell ratio | 1.8% | 4.6% | 0% |
| Outcome | UP only | UP only | YES + NO + UP |

### How they work together

| Wallet | Strategy | Horizon | Bot equivalent |
|--------|----------|---------|----------------|
| 0x1979ae6b | Up/down scalping | 5/15min | `terminal_convergence` + `wallet_copy` |
| 0x1d003413 | Up/down + 1h | 5min, 1h | `terminal_convergence` + `wallet_copy` |
| 0x1461cc6e | Threshold / combo | 1h | `combinatorial_arb` |

**Complementary:** 0x1979ae6b and 0x1d003413 cover short-term up/down; 0x1461cc6e covers 1h threshold markets. Together they span different timeframes and market types.
