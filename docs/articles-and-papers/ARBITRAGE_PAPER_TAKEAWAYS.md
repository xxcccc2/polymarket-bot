# Takeaways from Kroer et al. (2016): Arbitrage-Free Combinatorial Market Making

*Source: [arXiv:1606.02825v2](https://arxiv.org/abs/1606.02825) — EC'16*

---

## Core Idea

**Arbitrage-free prices** = convex combinations of valid payoff vectors (the *marginal polytope* M). When market prices fall outside M, there is an arbitrage opportunity.

---

## Directly Applicable to Your `combinatorial_arb` Strategy

### 1. Monotonicity Constraints (Sec. 3.1 — Comparisons)

For threshold markets like "Will BTC reach $70k?" vs "Will BTC reach $68k?":

> **P(BTC > 70k) ≤ P(BTC > 68k)**

This is exactly what your strategy exploits. The paper formalizes it as:

- For comparisons of random variables X₁, X₂:
  - **P(X₁ ≤ x) ≤ P(X₁ < X₂) + P(X₂ ≤ x)** for all x
  - **P(X₁ ≤ x) ≤ P(X₁ ≤ X₂) + P(X₂ < x)** for all x

Your bot checks: if `price(BTC>70k) > price(BTC>68k)` → violation → buy the underpriced side.

### 2. Profit Guarantee (Proposition 2.4)

> *"The guaranteed profit of any trader is at most D(μ*‖θ) where μ* is the Bregman projection of θ on M. Furthermore, this profit is achieved by any trade δ* moving the market to a state θ* with p(θ*) = μ*."*

**Takeaway:** When you detect a violation, the profit is bounded by the "distance" from coherent prices. Larger violations → larger guaranteed profit (before fees).

### 3. Fast vs Complete Arbitrage Removal

The paper uses two stages:

| Stage | Method | Speed | Result |
|-------|--------|-------|--------|
| **LCMM** | Linear constraints | Fast (poly-time) | Partial removal |
| **FWMM** | Bregman projection via Frank-Wolfe | Slower (IP solver) | Full removal |

**Takeaway:** Your bot does the "fast" version — check linear monotonicity constraints. The paper shows that even partial removal (LCMM) improves forecast accuracy 2–12% over independent pricing. You don't need full projection; simple constraint checks are valuable.

---

## Additional Constraint Types (Sec. 3.1)

### Sums

If X = X₁ + X₂ + … + Xₙ, then:

> ∑ x · P(X=x) = ∑ⱼ ∑ₓⱼ xⱼ · P(Xⱼ=xⱼ)

**Idea:** Markets on "total wins" or "sum of outcomes" must satisfy this. Could extend your strategy to sum constraints if Polymarket offers such markets.

### Subset / Conjunction

> **P(A ∧ B) ≤ min(P(A), P(B))**

**Idea:** "BTC > 100k AND ETH > 5k" must be ≤ each marginal. Another constraint type to add if you find such markets.

---

## Empirical Results (Sec. 5)

- **Dataset:** NCAA 2010 basketball tournament, 88k trades, 5k securities, 2⁶³ outcomes
- **FWMM vs LCMM:** 2–12% improvement in forecast accuracy (log likelihood)
- **Budget/liquidity:** Optimal budget ~10–100; LCMM/FWMM less sensitive than independent markets
- **Information propagation:** Constraints correct wrong bets — "information propagation can correct wrong bets"

**Takeaway:** Even when you can't fully remove arbitrage, enforcing monotonicity improves prices. Your combo_arb is doing the right thing.

---

## Implementation Hints for Your Bot

1. **Tighter constraints:** The paper derives *tighter* LCMM constraints for comparisons (not just LP relaxation of IP). Your current monotonicity check is the core; you could add:
   - Transitivity: if A ≤ B and B ≤ C, then A ≤ C
   - For "BTC > 70k" and "BTC > 68k" and "BTC > 65k": P(70) ≤ P(68) ≤ P(65)

2. **Partial outcome propagation:** When some games/events resolve, you can "settle" securities (fix price to 0 or 1) and propagate — reduces the space of valid payoffs. Polymarket does this automatically, but the idea: resolved events shrink the arbitrage surface.

3. **Edge sizing:** The paper's profit guarantee suggests sizing by "distance to coherence" — larger violation → larger bet. **Implemented:** `COMBO_EDGE_SIZE_FACTOR` and `COMBO_EDGE_SIZE_CAP` scale size by edge_cents. Default: 10¢ edge → 1.05x, 20¢ → 1.1x, capped at 2x.

4. **Full-pair violation check:** **Implemented:** Check all pairs in a threshold chain (not just adjacent) and keep the violation with max edge per underpriced token. Finds the best arbitrage in a chain (e.g. 70k vs 65k when 68k is in between).

---

## Citation

> Kroer, C., Dudík, M., Lahaie, S., & Balakrishnan, S. (2016). Arbitrage-Free Combinatorial Market Making via Integer Programming. *EC'16*, Maastricht.
