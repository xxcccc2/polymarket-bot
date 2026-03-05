# Epic Trades

Documentation of standout trades from the Polymarket bot.

---

## 2026-03-01: Bitcoin 2¢ → 100¢ (50×)

### Summary

| Field | Value |
|-------|-------|
| **Date** | 2026-03-01 |
| **Market** | Bitcoin Up or Down - March 1, 10AM ET |
| **Token** | `41871825400826364109041620306965769976518442424089490934899951129025709556493` |
| **Strategy** | `wallet_copy` |
| **Source wallet** | `0x1d003413...8b0313` (Canine-Commandment) |
| **Entry** | 2¢ (0.02) |
| **Exit** | 100¢ (1.00) — resolved YES |
| **Size** | 250 shares, $5.00 notional |
| **Return** | ~$250 (~4,900%) |
| **Wallet** | CHR |

### How It Happened

**Source:** **wallet_copy** — copied from **Canine-Commandment** (`0x1d0034134e339a309700ff2d34e99fa2d48b0313`).

**Evidence:** Order record in `data/bot_state_chr.sqlite`:

```
order_id: 0xbdef3dc0d554fc8742d43b84742c97e4d8bc18a4e61c14a07faa4385822a3263
token_id: 41871825400826364109041620306965769976518442424089490934899951129025709556493
side: BUY, price: 0.02, size: 250.0, status: filled
metadata: {"strategy": "wallet_copy", "trader": "Canine-Commandment", "trader_wallet": "0x1d0034134e339a309700ff2d34e99fa2d48b0313"}
```

Canine-Commandment bought the 2¢ YES; the bot copied it. YES resolved to 100¢.

### Config at Time of Trade

```
TRACKED_WALLETS=...0x1d0034134e339a309700ff2d34e99fa2d48b0313...
WALLET_COPY_SIZE_USD=5
MIN_PRICE_CENTS=0.99
```

---

*Add new epic trades below.*
