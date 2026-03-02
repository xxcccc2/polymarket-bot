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

## Compare

| Metric | 0x1979ae6b | 0x1d003413 |
|--------|------------|------------|
| Focus | 5/15min BTC | 5min + 1h BTC |
| Cheap buys (<20¢) | 26% | 13% |
| Expensive (50¢+) | 46% | 45% |
| Avg size | $14 | $37 |
| Sell ratio | 1.8% | 4.6% |
| Outcome bias | UP only | UP only |

**Shared patterns:** Both hold to resolution, favor UP, trade BTC short-term.
