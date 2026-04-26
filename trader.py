"""
IMC Prosperity 4 – Round 3 Trader  (v3)
Products: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000..VEV_6500

Strategy
────────
HYDROGEL_PACK       : mean-revert to FV=9991, ±6 quotes, position skew
VELVETFRUIT_EXTRACT : market-make ±3, also used as delta hedge for options
Vouchers (VEV_*)    : Black-Scholes at σ=30.3% (back-solved from hist data)
  - Deep ITM (4000,4500): priced at intrinsic S-K, taker-only
  - Near-ATM (5000-5400): take edge >threshold, MM ±1 with soft cap ±50
  - VEV_5500: take-only (thin spread, adverse selection risk)
  - VEV_6000/6500: skip (worthless)
  - Delta hedge: after voucher activity, trade VEV to neutralise net delta

v3 critical fix
───────────────
Live round timestamps: 0 → 99,900 (1,000 ticks, step 100).
TICKS_PER_DAY must be 100,000 (= 1,000 ticks × 100 units/tick).
v1/v2 had 10,000 → TTE hit zero at ts=50,000 (halfway through the round),
pricing every OTM option at 0 and sending the delta hedge haywire.
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


def bs_delta(S: float, K: float, T: float, sigma: float = 0.303) -> float:
    if T <= 0.0 or sigma <= 0.0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


# ── Constants ─────────────────────────────────────────────────────────────────

SIGMA = 0.303

# Live round: timestamps 0 → 99,900 in steps of 100  (1,000 ticks/round)
# 1 round-day = 1,000 ticks × 100 ts-units = 100,000 ts-units
TICKS_PER_DAY   = 100_000
ROUND3_TTE_DAYS = 5          # TTE at start of Round 3

HYDROGEL_FV    = 9991
HYDROGEL_HALF  = 6
HYDROGEL_LIMIT = 200

VEV_HALF  = 3
VEV_LIMIT = 200

# strike → (mode, take_thresh, mm_half, soft_limit)
VOUCHER_CFG = {
    4000: ("intrinsic", 1.0, 0, 30),
    4500: ("intrinsic", 1.0, 0, 30),
    5000: ("mm",        2.0, 1, 50),
    5100: ("mm",        2.0, 1, 50),
    5200: ("mm",        1.5, 1, 50),
    5300: ("mm",        1.5, 1, 50),
    5400: ("mm",        1.0, 1, 50),
    5500: ("take_only", 2.0, 0, 30),
    6000: ("skip",      0,   0,  0),
    6500: ("skip",      0,   0,  0),
}

VOUCHER_LIMIT = 300  # exchange hard limit


# ── Helpers ───────────────────────────────────────────────────────────────────

def mid(od: OrderDepth) -> float | None:
    b, a = od.buy_orders, od.sell_orders
    if b and a:
        return (max(b) + min(a)) / 2.0
    return float(max(b)) if b else (float(min(a)) if a else None)


def tte(timestamp: int, tte_base: float = ROUND3_TTE_DAYS,
        tpd: float = TICKS_PER_DAY) -> float:
    """Time-to-expiry in years.  tpd is timestamp-units per round-day."""
    return max((tte_base - timestamp / tpd) / 365.0, 1e-6)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ── HYDROGEL_PACK ─────────────────────────────────────────────────────────────

def trade_hydrogel(od: OrderDepth, position: int, orders: list) -> None:
    fv  = HYDROGEL_FV
    pos = position
    skew = clamp(-pos * 0.03, -HYDROGEL_HALF, HYDROGEL_HALF)

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

    bq = min(HYDROGEL_LIMIT - pos, 20)
    sq = min(pos + HYDROGEL_LIMIT, 20)
    if bq > 0:
        orders.append(Order("HYDROGEL_PACK", int(fv + skew - HYDROGEL_HALF), bq))
    if sq > 0:
        orders.append(Order("HYDROGEL_PACK", int(fv + skew + HYDROGEL_HALF) + 1, -sq))


# ── VELVETFRUIT_EXTRACT ───────────────────────────────────────────────────────

def trade_vev(od: OrderDepth, position: int,
              orders: list, hedge_qty: int = 0) -> int:
    """Market-make VEV + execute delta hedge. Returns updated position."""
    m = mid(od)
    if m is None:
        return position
    pos = position

    # Delta hedge first (priority fills)
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

    # Normal market-making
    skew = clamp(-pos * 0.05, -VEV_HALF, VEV_HALF)

    for px in sorted(od.sell_orders):
        if px >= m - VEV_HALF or pos >= VEV_LIMIT:
            break
        qty = min(-od.sell_orders[px], VEV_LIMIT - pos)
        if qty > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", px, qty))
            pos += qty

    for px in sorted(od.buy_orders, reverse=True):
        if px <= m + VEV_HALF or pos <= -VEV_LIMIT:
            break
        qty = min(od.buy_orders[px], pos + VEV_LIMIT)
        if qty > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", px, -qty))
            pos -= qty

    bq = min(VEV_LIMIT - pos, 15)
    sq = min(pos + VEV_LIMIT, 15)
    if bq > 0:
        orders.append(Order("VELVETFRUIT_EXTRACT", math.floor(m + skew - VEV_HALF), bq))
    if sq > 0:
        orders.append(Order("VELVETFRUIT_EXTRACT", math.ceil(m + skew + VEV_HALF), -sq))

    return pos


# ── Vouchers ──────────────────────────────────────────────────────────────────

def trade_voucher(symbol: str, strike: int, od: OrderDepth,
                  position: int, S: float, T: float,
                  orders: list) -> tuple[int, float]:
    """Returns (new_pos, delta_change)."""
    mode, take_thresh, mm_half, soft_lim = VOUCHER_CFG[strike]
    if mode == "skip":
        return position, 0.0

    fv    = max(0.0, S - strike) if mode == "intrinsic" else bs_call(S, strike, T)
    delta = (1.0 if S > strike else 0.0) if mode == "intrinsic" else bs_delta(S, strike, T)
    cap   = min(soft_lim, VOUCHER_LIMIT)
    pos   = position
    d_chg = 0.0

    # Take cheap asks
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

        # TTE — read optional overrides injected by backtest
        tte_base = td.get("tte_base", ROUND3_TTE_DAYS)
        tpd      = td.get("tpd",      TICKS_PER_DAY)
        T = tte(state.timestamp, tte_base, tpd)

        # Underlying price
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
                                       pos, S, T, o)
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
