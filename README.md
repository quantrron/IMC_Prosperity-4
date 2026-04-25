# IMC Prosperity 4 – Algorithmic Trading Bot

Automated market-making and options-pricing strategy for the IMC Prosperity 4 competition.

## Products (Round 3)

| Product | Strategy | Notes |
|---|---|---|
| `HYDROGEL_PACK` | Mean-reversion market maker | FV = 9991, ±6 spread, position skew |
| `VELVETFRUIT_EXTRACT` (VEV) | Tight market maker | FV ≈ 5250, ±3 spread, slight long bias |
| `VEV_4000` / `VEV_4500` | Intrinsic value | Deep ITM: price = S − K |
| `VEV_5000` – `VEV_5500` | Black-Scholes MM + taking | IV = 30.3%, flat smile |
| `VEV_6000` / `VEV_6500` | Skip | Worthless (always ~0.5) |

## Options Pricing

All vouchers (VEV_*) are European call options on VELVETFRUIT_EXTRACT.

```
Fair value = BS_call(S, K, T, σ=0.303)

S     = current VELVETFRUIT_EXTRACT mid
K     = strike (4000 / 4500 / 5000 / 5100 / 5200 / 5300 / 5400 / 5500)
T     = (5 − timestamp / 10000) / 365   [days remaining → years]
σ     = 0.303  (30.3% — back-solved from historical data, flat smile)
```

Implied volatility was back-solved from Round 3 historical price data and is **30.3%** (not the 23.4% sometimes cited), with a perfectly flat smile across all strikes.

## Position Limits

| Product | Limit |
|---|---|
| HYDROGEL_PACK | ±200 |
| VELVETFRUIT_EXTRACT | ±200 |
| Each voucher | ±300 |

## File Layout

```
trader.py        ← main submission file (Trader class)
datamodel.py     ← local stub of the Prosperity datamodel
test_trader.py   ← smoke-test / sanity checks
ROUND_3/         ← historical CSV data (excluded from git)
```

## Running Tests

```bash
python3 test_trader.py
```
