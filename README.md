<div align="center">

# IMC Prosperity

<img src="https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python&logoColor=white"/>
<img src="https://img.shields.io/badge/Dependencies-Zero-brightgreen?style=flat-square"/>
<img src="https://img.shields.io/badge/Domain-Options%20%7C%20Market%20Making%20%7C%20Vol%20Arb-lightgrey?style=flat-square"/>

**Algorithmic trading bot for the IMC Prosperity competition**

*All mathematics implemented from scratch — no external libraries*

</div>

---

## Overview

Trading system built iteratively across competition rounds for three asset classes: a physical commodity traded via mean-reversion market making, a spot instrument used for delta hedging, and a multi-strike European call option chain exploiting implied vs. realised volatility divergence. Every primitive — BSM pricing, IV inversion, order execution — written in pure Python with zero dependencies.

---

## Strategies

**Mean-reversion market making** — quotes two-sided markets around a fixed fair value with calibrated bands. Inventory skew shifts bid/ask asymmetrically based on current position to prevent accumulation. Aggressive fills taken when market price deviates beyond the band edge.

**Delta hedging** — net portfolio delta aggregated across all option legs and continuously offset via the spot instrument. Later deliberately dropped after backtest analysis showed hedging costs exceeded the exposure risk — an empirical decision, not an oversight.

**Volatility arbitrage across a call option chain** — options priced via BSM using calibrated implied vol; IV back-solved from live midpoints each tick via bisection search to build a dynamic smile. Position sizing scaled by relative overpricing across strikes. Strategy toggles between shorting high-OTM calls (theta collection, break-evens above observed historical highs) and buying lower strikes (mean-reversion bet on spot recovery) based on spot price movement.

**Counterparty behaviour detection** — tracks specific participant activity in the live trade feed and adjusts execution thresholds dynamically based on inferred order flow direction — a basic adverse selection filter.

---

## Methods

**Black-Scholes from scratch** — closed-form implementation, normal CDF approximated via error function, no pricing library used:

$$C = S \cdot \mathcal{N}(d_1) - K \cdot \mathcal{N}(d_2) \qquad \Delta = \mathcal{N}(d_1) \qquad \mathcal{N}(x) \approx \tfrac{1}{2}\!\left[1 + \text{erf}\!\left(\tfrac{x}{\sqrt{2}}\right)\right]$$

**Implied volatility solver** — bisection root-finding to back-solve IV from live bid/ask midpoints each tick.

**Adaptive phase switching** — hysteretic trigger toggles options strategy between two regimes to prevent oscillation.

---

## Structure

```
IMC_Prosperity-4/
├── round3/
│   ├── trader.py          BSM pricing, delta hedging, vol arb
│   └── backtest.py        CSV replay harness, mark-to-market PnL
└── round4/
    ├── trader.py          counterparty detection, phase switching
    └── backtest.py        updated backtesting harness
```

---

<div align="center">
<sub>Built for the IMC Prosperity trading competition · Research and educational implementation</sub>
</div>
