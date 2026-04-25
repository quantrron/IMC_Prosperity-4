"""
IMC Prosperity 4 – Round 3 Trader
Products: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000..VEV_6500

Strategy
────────
HYDROGEL_PACK       : mean-revert to FV=9991, ±6 quotes, position skew
VELVETFRUIT_EXTRACT : market-make ±3, also used as delta hedge for options
Vouchers (VEV_*)    : Black-Scholes at σ=30.3% (back-solved from hist data)
  - Deep ITM (4000,4500): priced at intrinsic S-K, taker-only
  - Liquid near-ATM (5000-5400): take edge >2.0, MM ±1 with soft cap ±50
  - VEV_5500: take only (too cheap, spread too thin, adverse selection)
  - VEV_6000/6500: skip (worthless)
  - Delta hedge: after voucher activity, trade VEV to neutralise net delta

Key fix vs v1: position capped at ±50 per voucher, delta-hedge via VEV,
skew capped at ±1 tick so it never crosses fair value.
"""

import json
import math
from datamodel import (
    Order, OrderDepth, Symbol, TradingState,
)

# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float = 0.303) -> float:
    if T <= 0.0 or sigma <= 0.0:
        return max(0.0, S - K)
    sv = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / sv
    d2 = d1 - sv
    return S * _norm_cdf(d1) - K * _norm_cdf(d2)


def bs_delta(S: float, K: float, T: float, sigma: float = 0.303) -> float:
    """dC/dS — how much the call price moves per 1-unit move in underlying."""
    if T <= 0.0 or sigma <= 0.0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


# ── Constants ─────────────────────────────────────────────────────────────────

SIGMA          = 0.303
TICKS_PER_DAY  = 10_000
ROUND3_TTE_DAYS = 5

HYDROGEL_FV    = 9991
HYDROGEL_HALF  = 6
HYDROGEL_LIMIT = 200

VEV_HALF       = 3
VEV_LIMIT      = 200

# Voucher config per strike
# fmt: off
VOUCHER_CFG = {
    # strike: (mode, take_thresh, mm_half, soft_limit)
    # mode: "intrinsic" | "mm" | "take_only" | "skip"
    4000: ("intrinsic", 1.0, 0,  30),
    4500: ("intrinsic", 1.0, 0,  30),
    5000: ("mm",        2.0, 1,  50),
    5100: ("mm",        2.0, 1,  50),
    5200: ("mm",        1.5, 1,  50),
    5300: ("mm",        1.5, 1,  50),
    5400: ("mm",        1.0, 1,  50),
    5500: ("take_only", 2.0, 0,  30),
    6000: ("skip",      0,   0,   0),
    6500: ("skip",      0,   0,   0),
}
# fmt: on

VOUCHER_LIMIT = 300  # hard exchange limit


# ── Helpers ───────────────────────────────────────────────────────────────────

def mid(od: OrderDepth) -> float | None:
    bids, asks = od.buy_orders, od.sell_orders
    if bids and asks:
        return (max(bids) + min(asks)) / 2.0
    if bids:
        return float(max(bids))
    if asks:
        return float(min(asks))
    return None


def tte(timestamp: int) -> float:
    days_left = ROUND3_TTE_DAYS - timestamp / TICKS_PER_DAY
    return max(days_left / 365.0, 1e-6)


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


# ── Product logic ─────────────────────────────────────────────────────────────

def trade_hydrogel(od: OrderDepth, position: int, orders: list) -> None:
    fv  = HYDROGEL_FV
    pos = position
    skew = clamp(-pos * 0.03, -HYDROGEL_HALF, HYDROGEL_HALF)

    for ask_px in sorted(od.sell_orders):
        if ask_px < fv - HYDROGEL_HALF and pos < HYDROGEL_LIMIT:
            qty = min(-od.sell_orders[ask_px], HYDROGEL_LIMIT - pos)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", ask_px, qty))
                pos += qty

    for bid_px in sorted(od.buy_orders, reverse=True):
        if bid_px > fv + HYDROGEL_HALF and pos > -HYDROGEL_LIMIT:
            qty = min(od.buy_orders[bid_px], pos + HYDROGEL_LIMIT)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", bid_px, -qty))
                pos -= qty

    buy_qty  = min(HYDROGEL_LIMIT - pos, 20)
    sell_qty = min(pos + HYDROGEL_LIMIT, 20)
    if buy_qty > 0:
        orders.append(Order("HYDROGEL_PACK", int(fv + skew - HYDROGEL_HALF), buy_qty))
    if sell_qty > 0:
        orders.append(Order("HYDROGEL_PACK", int(fv + skew + HYDROGEL_HALF) + 1, -sell_qty))


def trade_vev(od: OrderDepth, position: int, orders: list,
              delta_hedge_qty: int = 0) -> int:
    """
    Market-make VEV and also execute delta hedge orders.
    Returns the executed position after hedging.
    delta_hedge_qty: positive = need to buy, negative = need to sell.
    """
    m = mid(od)
    if m is None:
        return position

    pos = position

    # ── Delta hedge first (priority) ──────────────────────────────────────
    if delta_hedge_qty > 0:
        # Need to buy VEV to hedge
        for ask_px in sorted(od.sell_orders):
            if pos >= VEV_LIMIT:
                break
            qty = min(-od.sell_orders[ask_px], delta_hedge_qty, VEV_LIMIT - pos)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", ask_px, qty))
                pos += qty
                delta_hedge_qty -= qty
    elif delta_hedge_qty < 0:
        # Need to sell VEV to hedge
        need = -delta_hedge_qty
        for bid_px in sorted(od.buy_orders, reverse=True):
            if pos <= -VEV_LIMIT:
                break
            qty = min(od.buy_orders[bid_px], need, pos + VEV_LIMIT)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", bid_px, -qty))
                pos -= qty
                need -= qty

    # ── Normal market-making ──────────────────────────────────────────────
    skew = clamp(-pos * 0.05, -VEV_HALF, VEV_HALF)

    for ask_px in sorted(od.sell_orders):
        if ask_px < m - VEV_HALF and pos < VEV_LIMIT:
            qty = min(-od.sell_orders[ask_px], VEV_LIMIT - pos)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", ask_px, qty))
                pos += qty

    for bid_px in sorted(od.buy_orders, reverse=True):
        if bid_px > m + VEV_HALF and pos > -VEV_LIMIT:
            qty = min(od.buy_orders[bid_px], pos + VEV_LIMIT)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", bid_px, -qty))
                pos -= qty

    buy_qty  = min(VEV_LIMIT - pos, 15)
    sell_qty = min(pos + VEV_LIMIT, 15)
    if buy_qty > 0:
        orders.append(Order("VELVETFRUIT_EXTRACT", math.floor(m + skew - VEV_HALF), buy_qty))
    if sell_qty > 0:
        orders.append(Order("VELVETFRUIT_EXTRACT", math.ceil(m + skew + VEV_HALF), -sell_qty))

    return pos


def trade_voucher(
    symbol: str, strike: int,
    od: OrderDepth, position: int,
    S: float, T: float,
    orders: list,
) -> tuple[int, float]:
    """
    Returns (new_position, delta_exposure_change).
    delta_exposure_change is the extra delta we picked up this tick
    (positive = we bought calls = more long delta from options).
    """
    mode, take_thresh, mm_half, soft_lim = VOUCHER_CFG[strike]

    if mode == "skip":
        return position, 0.0

    if mode == "intrinsic":
        fv = max(0.0, S - strike)
    else:
        fv = bs_call(S, strike, T, SIGMA)

    delta = bs_delta(S, strike, T, SIGMA) if mode != "intrinsic" else (1.0 if S > strike else 0.0)

    pos = position
    delta_picked_up = 0.0

    # ── Taking: buy cheap asks ────────────────────────────────────────────
    for ask_px in sorted(od.sell_orders):
        edge = fv - ask_px
        if edge < take_thresh:
            break
        if pos >= min(soft_lim, VOUCHER_LIMIT):
            break
        qty = min(-od.sell_orders[ask_px], min(soft_lim, VOUCHER_LIMIT) - pos)
        if qty > 0:
            orders.append(Order(symbol, ask_px, qty))
            pos += qty
            delta_picked_up += qty * delta

    # ── Taking: sell expensive bids ───────────────────────────────────────
    for bid_px in sorted(od.buy_orders, reverse=True):
        edge = bid_px - fv
        if edge < take_thresh:
            break
        if pos <= -min(soft_lim, VOUCHER_LIMIT):
            break
        qty = min(od.buy_orders[bid_px], pos + min(soft_lim, VOUCHER_LIMIT))
        if qty > 0:
            orders.append(Order(symbol, bid_px, -qty))
            pos -= qty
            delta_picked_up -= qty * delta

    # ── Making (only for mm mode, and only within soft limit) ────────────
    if mode == "mm":
        # Skew: max ±1 tick so we never cross FV
        raw_skew = -pos * 0.01
        skew = clamp(raw_skew, -mm_half, mm_half)

        bid_px = math.floor(fv + skew - mm_half)
        ask_px = math.ceil(fv + skew + mm_half)

        # Hard floor: never bid above FV, never ask below FV
        bid_px = min(bid_px, math.floor(fv) - 1)
        ask_px = max(ask_px, math.ceil(fv) + 1)
        bid_px = max(bid_px, 1)
        if ask_px <= bid_px:
            ask_px = bid_px + 1

        # Only quote on side that won't push us past soft limit
        buy_qty  = min(5, soft_lim - pos) if pos < soft_lim else 0
        sell_qty = min(5, pos + soft_lim) if pos > -soft_lim else 0

        if buy_qty > 0:
            orders.append(Order(symbol, bid_px, buy_qty))
        if sell_qty > 0:
            orders.append(Order(symbol, ask_px, -sell_qty))

    return pos, delta_picked_up


# ── Main Trader ────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> tuple[dict[Symbol, list[Order]], int, str]:
        result: dict[Symbol, list[Order]] = {}

        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        T = tte(state.timestamp)

        # Current underlying price
        vev_od = state.order_depths.get("VELVETFRUIT_EXTRACT")
        S = mid(vev_od) if vev_od else td.get("last_S", 5250.0)
        if S is None:
            S = td.get("last_S", 5250.0)
        td["last_S"] = S

        # ── HYDROGEL_PACK ──────────────────────────────────────────────────
        if "HYDROGEL_PACK" in state.order_depths:
            orders: list[Order] = []
            trade_hydrogel(
                state.order_depths["HYDROGEL_PACK"],
                state.position.get("HYDROGEL_PACK", 0),
                orders,
            )
            if orders:
                result["HYDROGEL_PACK"] = orders

        # ── Vouchers + delta hedge ─────────────────────────────────────────
        # First pass: trade all vouchers, accumulate net delta exposure
        net_option_delta = 0.0
        for strike, (mode, *_) in VOUCHER_CFG.items():
            if mode == "skip":
                continue
            symbol = f"VEV_{strike}"
            if symbol not in state.order_depths:
                continue

            orders = []
            pos = state.position.get(symbol, 0)
            new_pos, delta_change = trade_voucher(
                symbol, strike,
                state.order_depths[symbol],
                pos, S, T, orders,
            )
            if orders:
                result[symbol] = orders

            # Accumulate total option delta from current positions + new trades
            # (use new_pos as the position we'll carry)
            d = bs_delta(S, strike, T, SIGMA) if mode != "intrinsic" else (1.0 if S > strike else 0.0)
            net_option_delta += new_pos * d

        # Delta hedge target: VEV position should offset option delta
        # net_option_delta > 0 means we're long calls = long delta from options
        # we want to sell VEV to hedge (be short VEV)
        # hedge_target = -round(net_option_delta)
        vev_pos = state.position.get("VELVETFRUIT_EXTRACT", 0)
        hedge_target = -round(net_option_delta)
        hedge_target = clamp(hedge_target, -VEV_LIMIT, VEV_LIMIT)
        delta_hedge_qty = int(hedge_target - vev_pos)  # how much to buy/sell

        # ── VELVETFRUIT_EXTRACT ────────────────────────────────────────────
        if vev_od:
            orders = []
            trade_vev(vev_od, vev_pos, orders, delta_hedge_qty)
            if orders:
                result["VELVETFRUIT_EXTRACT"] = orders

        return result, 0, json.dumps(td)
