"""
IMC Prosperity 4 – Round 3 Trader  (v3)
Products: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000..VEV_6500

Strategy
────────
HYDROGEL_PACK       : mean-revert to FV=9991, WIDE ±20 bands so we only trade
                      at genuine extremes (avoids whipsawing on small moves)
VELVETFRUIT_EXTRACT : market-make at mid ±2 (inside spread), delta hedge
Vouchers (VEV_*)    : Black-Scholes at σ=30.3% (back-solved from hist data)
  - Deep ITM (4000,4500): buy at intrinsic (taker), small positions
  - 5000: MM ±1 around BS fair value
  - Near-ATM (5100-5400): SHORT calls to collect theta
      Rationale: realized vol in live round ≈ 13% << implied 30.3% → theta
      far exceeds gamma cost → short options are profitable
  - 5500+: skip
  - Delta hedge: net portfolio delta ≈ 0 (long ITM offsets short near-ATM),
      minimal VEV hedging needed

v3 critical fixes vs v2
───────────────────────
1. HYDROGEL_HALF 6→20: v2 shorted immediately at ts=0 (bid 10003 > 9997),
   accumulated -200 before the spike to 10027 → big MTM loss. Wide band waits
   for genuine extremes (>10011 to sell, <9971 to buy).

2. VEV_HALF 3→2: v2 quoted OUTSIDE the 5265/5270 market (bids at 5264, asks
   at 5271), earning almost nothing from MM. HALF=2 quotes AT the market spread.

3. Near-ATM options flipped to SHORT: v2 bought calls and paid theta. With
   only 1000 live ticks and realized vol 13%, long calls lose time value.
   Short calls collect it instead.
"""

import json
import math
from datamodel import Order, OrderDepth, Symbol, TradingState

# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float = 0.303) -> float:
    if T <= 0.0 or sigma <= 0.0:
        return max(0.0, S - K)
    sv = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / sv
    return S * _norm_cdf(d1) - K * _norm_cdf(d1 - sv)


def bs_iv(S: float, K: float, T: float, market_price: float,
          lo: float = 0.01, hi: float = 5.0, tol: float = 1e-4) -> float:
    """Bisection IV solver.  Returns implied vol or SIGMA fallback."""
    if T <= 0 or market_price <= 0:
        return SIGMA
    # Quick bracket check
    if bs_call(S, K, T, hi) < market_price:
        return hi
    for _ in range(40):
        mid_s = (lo + hi) * 0.5
        if bs_call(S, K, T, mid_s) > market_price:
            hi = mid_s
        else:
            lo = mid_s
        if hi - lo < tol:
            break
    return (lo + hi) * 0.5


def bs_delta(S: float, K: float, T: float, sigma: float = 0.303) -> float:
    if T <= 0.0 or sigma <= 0.0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


# ── Constants ─────────────────────────────────────────────────────────────────

SIGMA = 0.303


# ── Time-to-expiry calibration ────────────────────────────────────────────────
# Sandbox  : ts 0 → 99,900  (1,000 ticks × 100)  — 1/10 of a round-day
# Final eval: ts 0 → 999,900 (10,000 ticks × 100) — full round-day (like CSVs)
#
# TICKS_PER_DAY = 1,000,000 is correct for BOTH:
#   • Sandbox  → TTE barely changes (0.1 day consumed) — options stay priced right
#   • Final eval → TTE goes from 5d to ~4d over the full run — no collapse at midpoint
#
# Old value 100,000 was calibrated only for the sandbox; in the final eval it
# caused TTE=0 at ts=500,000, pricing all OTM calls at $0 for the back half.
TICKS_PER_DAY   = 1_000_000   # matches HIST_TPD — safe for both sandbox & final eval
ROUND3_TTE_DAYS = 5

HYDROGEL_FV    = 9991
HYDROGEL_HALF  = 20         # wide band: only sell >10011, only buy <9971
HYDROGEL_LIMIT = 200

VEV_HALF  = 2               # quote at mid±2 to be inside market spread
VEV_LIMIT = 200

# strike → (mode, take_thresh, mm_half, soft_limit)
#
# "short" mode: SELL calls to bids when fv-bid <= take_thresh
#   - near-ATM options: realized vol (13%) << implied vol (30.3%)
#     → theta >> gamma cost → collecting premium is profitable
#   - soft_lim=20 keeps delta exposure small; long ITM largely offsets
VOUCHER_CFG = {
    4000: ("intrinsic", 1.0,  0,  30),
    4500: ("intrinsic", 1.0,  0,  30),
    5000: ("short",    10.0,  0,  30),
    5100: ("short",    15.0,  0,  50),
    5200: ("short",    15.0,  0,  75),
    5300: ("short",    15.0,  0, 300),
    5400: ("short",    15.0,  0, 300),
    5500: ("skip",      0,    0,   0),
    6000: ("skip",      0,    0,   0),
    6500: ("skip",      0,    0,   0),
}

VOUCHER_LIMIT = 300


# ── Helpers ───────────────────────────────────────────────────────────────────

def mid(od: OrderDepth) -> float | None:
    b, a = od.buy_orders, od.sell_orders
    if b and a:
        return (max(b) + min(a)) / 2.0
    return float(max(b)) if b else (float(min(a)) if a else None)


def tte(timestamp: int, tte_base: float = ROUND3_TTE_DAYS,
        tpd: float = TICKS_PER_DAY) -> float:
    return max((tte_base - timestamp / tpd) / 365.0, 1e-6)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ── HYDROGEL_PACK ─────────────────────────────────────────────────────────────

def trade_hydrogel(od: OrderDepth, position: int, orders: list) -> None:
    fv  = HYDROGEL_FV
    pos = position
    skew = clamp(-pos * 0.03, -HYDROGEL_HALF, HYDROGEL_HALF)

    # Take only when price is genuinely extreme (±20 from FV)
    for px in sorted(od.sell_orders):
        if px >= fv - HYDROGEL_HALF or pos >= HYDROGEL_LIMIT:
            break
        qty = min(-od.sell_orders[px], HYDROGEL_LIMIT - pos)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", px, qty))
            pos += qty

    for px in sorted(od.buy_orders, reverse=True):
        if px <= fv + HYDROGEL_HALF or pos <= -HYDROGEL_LIMIT:
            break
        qty = min(od.buy_orders[px], pos + HYDROGEL_LIMIT)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", px, -qty))
            pos -= qty

    # Passive MM quotes at ±HALF: fill when market reaches these extremes
    bq = min(HYDROGEL_LIMIT - pos, 20)
    sq = min(pos + HYDROGEL_LIMIT, 20)
    if bq > 0:
        orders.append(Order("HYDROGEL_PACK", int(fv + skew - HYDROGEL_HALF), bq))
    if sq > 0:
        orders.append(Order("HYDROGEL_PACK", int(fv + skew + HYDROGEL_HALF) + 1, -sq))


# ── VELVETFRUIT_EXTRACT ───────────────────────────────────────────────────────

def trade_vev(od: OrderDepth, position: int,
              orders: list, hedge_qty: int = 0) -> int:
    """Delta hedge only — no market-making on VEV to avoid adverse selection."""
    pos = position

    if hedge_qty > 0:
        for px in sorted(od.sell_orders):
            if pos >= VEV_LIMIT or hedge_qty <= 0:
                break
            qty = min(-od.sell_orders[px], hedge_qty, VEV_LIMIT - pos)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", px, qty))
                pos += qty
                hedge_qty -= qty
    elif hedge_qty < 0:
        need = -hedge_qty
        for px in sorted(od.buy_orders, reverse=True):
            if pos <= -VEV_LIMIT or need <= 0:
                break
            qty = min(od.buy_orders[px], need, pos + VEV_LIMIT)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", px, -qty))
                pos -= qty
                need -= qty

    return pos


# ── Vouchers ──────────────────────────────────────────────────────────────────

def trade_voucher(symbol: str, strike: int, od: OrderDepth,
                  position: int, S: float, T: float,
                  orders: list, cap_override: int | None = None) -> tuple[int, float]:
    """Returns (new_pos, delta_change).
    cap_override: if set, replaces soft_lim from VOUCHER_CFG (used for IV-smile sizing)."""
    mode, take_thresh, mm_half, soft_lim = VOUCHER_CFG[strike]
    if mode == "skip":
        return position, 0.0

    fv    = max(0.0, S - strike) if mode == "intrinsic" else bs_call(S, strike, T)
    delta = (1.0 if S > strike else 0.0) if mode == "intrinsic" else bs_delta(S, strike, T)
    cap   = min(cap_override if cap_override is not None else soft_lim, VOUCHER_LIMIT)
    pos   = position
    d_chg = 0.0

    if mode == "short":
        # Sell calls to bids when bid is not too far below BS fair value.
        # Collects theta since realized vol << implied vol in the live round.
        for px in sorted(od.buy_orders, reverse=True):
            if fv - px > take_thresh or pos <= -cap:
                break
            qty = min(od.buy_orders[px], pos + cap)
            if qty > 0:
                orders.append(Order(symbol, px, -qty))
                pos   -= qty
                d_chg -= qty * delta
        return pos, d_chg

    # Take cheap asks (long modes: intrinsic, mm, take_only)
    for px in sorted(od.sell_orders):
        if fv - px < take_thresh or pos >= cap:
            break
        qty = min(-od.sell_orders[px], cap - pos)
        if qty > 0:
            orders.append(Order(symbol, px, qty))
            pos   += qty
            d_chg += qty * delta

    # Take expensive bids
    for px in sorted(od.buy_orders, reverse=True):
        if px - fv < take_thresh or pos <= -cap:
            break
        qty = min(od.buy_orders[px], pos + cap)
        if qty > 0:
            orders.append(Order(symbol, px, -qty))
            pos   -= qty
            d_chg -= qty * delta

    # Market-make (mm mode only)
    if mode == "mm":
        skew   = clamp(-pos * 0.01, -mm_half, mm_half)
        bid_px = min(math.floor(fv + skew - mm_half), math.floor(fv) - 1)
        ask_px = max(math.ceil(fv + skew + mm_half),  math.ceil(fv)  + 1)
        bid_px = max(bid_px, 1)
        if ask_px <= bid_px:
            ask_px = bid_px + 1

        bq = min(5, cap - pos) if pos < cap else 0
        sq = min(5, pos + cap) if pos > -cap else 0
        if bq > 0:
            orders.append(Order(symbol, bid_px, bq))
        if sq > 0:
            orders.append(Order(symbol, ask_px, -sq))

    return pos, d_chg


# ── Trader ────────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> tuple[dict[Symbol, list[Order]], int, str]:
        result: dict[Symbol, list[Order]] = {}

        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        tte_base = td.get("tte_base", ROUND3_TTE_DAYS)
        tpd      = td.get("tpd",      TICKS_PER_DAY)
        T = tte(state.timestamp, tte_base, tpd)

        vev_od = state.order_depths.get("VELVETFRUIT_EXTRACT")
        S = mid(vev_od) if vev_od else td.get("last_S", 5250.0)
        if S is None:
            S = td.get("last_S", 5250.0)
        td["last_S"] = S

        # HYDROGEL_PACK
        if "HYDROGEL_PACK" in state.order_depths:
            o: list[Order] = []
            trade_hydrogel(state.order_depths["HYDROGEL_PACK"],
                           state.position.get("HYDROGEL_PACK", 0), o)
            if o:
                result["HYDROGEL_PACK"] = o

        # ── IV-smile sizing for short strikes ────────────────────────────────
        # Compute implied vol per short strike, then scale soft_lim by how
        # much each strike's IV exceeds the average (higher IV = more overpriced
        # = short more aggressively).  Strikes with no market data use SIGMA.
        iv_map: dict[int, float] = {}
        for strike, (mode, _, _, soft_lim) in VOUCHER_CFG.items():
            if mode != "short":
                continue
            sym = f"VEV_{strike}"
            od_v = state.order_depths.get(sym)
            if od_v is None:
                iv_map[strike] = SIGMA
                continue
            mp = mid(od_v)
            iv_map[strike] = bs_iv(S, strike, T, mp) if mp is not None else SIGMA

        if iv_map:
            mean_iv = sum(iv_map.values()) / len(iv_map)
        else:
            mean_iv = SIGMA

        # cap_override for each short strike: base × (1 + 2×relative_deviation)
        # clamped to [0.5×base, 2×base]
        iv_soft: dict[int, int] = {}
        for strike, iv in iv_map.items():
            _, _, _, soft_lim = VOUCHER_CFG[strike]
            if mean_iv > 0:
                scale = clamp(1.0 + 2.0 * (iv - mean_iv) / mean_iv, 0.5, 2.0)
            else:
                scale = 1.0
            iv_soft[strike] = max(1, int(soft_lim * scale))

        # Vouchers — accumulate portfolio delta
        net_delta = 0.0
        for strike, (mode, *_) in VOUCHER_CFG.items():
            if mode == "skip":
                continue
            sym = f"VEV_{strike}"
            if sym not in state.order_depths:
                continue
            o = []
            pos = state.position.get(sym, 0)
            new_pos, _ = trade_voucher(sym, strike,
                                       state.order_depths[sym],
                                       pos, S, T, o,
                                       cap_override=iv_soft.get(strike))
            if o:
                result[sym] = o

            d = (1.0 if S > strike else 0.0) if mode == "intrinsic" else bs_delta(S, strike, T)
            net_delta += new_pos * d

        # Delta hedge via VEV
        vev_pos      = state.position.get("VELVETFRUIT_EXTRACT", 0)
        hedge_target = int(clamp(-round(net_delta), -VEV_LIMIT, VEV_LIMIT))
        hedge_qty    = hedge_target - vev_pos

        if vev_od:
            o = []
            trade_vev(vev_od, vev_pos, o, hedge_qty)
            if o:
                result["VELVETFRUIT_EXTRACT"] = o

        return result, 0, json.dumps(td)
