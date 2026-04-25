"""
IMC Prosperity 4 – Round 3 Trader
Products: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000..VEV_6500

Strategy summary
────────────────
HYDROGEL_PACK    : mean-revert to FV=9991, quote ±6 with position skew
VELVETFRUIT_EXTRACT : market-make around ~5250, slight long bias
Vouchers (VEV_*)  : Black-Scholes at IV=30.3%, take mispriced orders then
                    market-make tight around fair value.
                    Deep ITM (4000, 4500) priced at intrinsic S-K.
                    Worthless (6000, 6500) skip entirely.
"""

import json
import math
from datamodel import (
    Listing, Observation, Order, OrderDepth, ProsperityEncoder,
    Symbol, Trade, TradingState,
)


# ── Black-Scholes helpers ─────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float = 0.303, r: float = 0.0) -> float:
    """European call price via Black-Scholes."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


# ── Constants ─────────────────────────────────────────────────────────────────

SIGMA = 0.303          # implied volatility (flat smile, from historical data)
TICKS_PER_DAY = 10_000 # 10000 tick-units per competition day

# Round 3: 5 days TTE at the start, decreasing each tick
# timestamp goes 0..999900 in steps of 100 within a day
# day index within round: state.timestamp // TICKS_PER_DAY
# (the live round is day 0 of this round, with 5 days left)
ROUND3_TTE_DAYS = 5

HYDROGEL_FV   = 9991
HYDROGEL_HALF = 6     # half-spread for market making
HYDROGEL_LIMIT = 200

VEV_FV_BASE  = 5250   # fallback if no orderbook
VEV_HALF     = 3      # half-spread for VEV market making
VEV_LIMIT    = 200

VOUCHER_LIMIT     = 300
VOUCHER_STRIKES   = [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500]
DEEP_ITM_STRIKES  = {4000, 4500}   # price = intrinsic only
SKIP_STRIKES      = {6000, 6500}   # worthless, skip

# Take threshold: min edge needed to lift/hit a voucher order
# Wider threshold for illiquid/deep strikes, tighter for liquid ATM ones
TAKE_THRESH = {
    4000: 1.0, 4500: 1.0,
    5000: 2.0, 5100: 2.0,
    5200: 1.5, 5300: 1.5,
    5400: 1.0, 5500: 0.5,
}
# Market-making half-spread per strike
MM_HALF = {
    4000: 2, 4500: 2,
    5000: 3, 5100: 2,
    5200: 1, 5300: 1,
    5400: 1, 5500: 1,
}


# ── Utility ───────────────────────────────────────────────────────────────────

def mid(od: OrderDepth) -> float | None:
    """Best-bid/ask mid from order depth."""
    bids = od.buy_orders
    asks = od.sell_orders
    if bids and asks:
        return (max(bids) + min(asks)) / 2.0
    if bids:
        return float(max(bids))
    if asks:
        return float(min(asks))
    return None


def tte(timestamp: int) -> float:
    """Time-to-expiry in years for current timestamp."""
    days_elapsed = timestamp / TICKS_PER_DAY  # fractional days elapsed this round
    days_left = ROUND3_TTE_DAYS - days_elapsed
    return max(days_left / 365.0, 1e-6)


# ── Per-product logic ──────────────────────────────────────────────────────────

def trade_hydrogel(
    od: OrderDepth,
    position: int,
    orders: list[Order],
) -> None:
    """Mean-revert HYDROGEL_PACK around FV=9991."""
    fv = HYDROGEL_FV

    # Skew fair value based on position (positive pos → lower bid/ask to reduce)
    skew = -position * 0.03  # gentle skew
    bid_fv = fv + skew - HYDROGEL_HALF
    ask_fv = fv + skew + HYDROGEL_HALF

    pos = position

    # Take obviously mispriced orders first
    for ask_px, ask_vol in sorted(od.sell_orders.items()):
        if ask_px < fv - HYDROGEL_HALF and pos < HYDROGEL_LIMIT:
            qty = min(-ask_vol, HYDROGEL_LIMIT - pos)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", ask_px, qty))
                pos += qty

    for bid_px, bid_vol in sorted(od.buy_orders.items(), reverse=True):
        if bid_px > fv + HYDROGEL_HALF and pos > -HYDROGEL_LIMIT:
            qty = min(bid_vol, pos + HYDROGEL_LIMIT)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", bid_px, -qty))
                pos -= qty

    # Market-make around skewed FV
    buy_qty  = min(HYDROGEL_LIMIT - pos, 20)
    sell_qty = min(pos + HYDROGEL_LIMIT, 20)

    if buy_qty > 0:
        orders.append(Order("HYDROGEL_PACK", int(bid_fv), buy_qty))
    if sell_qty > 0:
        orders.append(Order("HYDROGEL_PACK", int(ask_fv) + 1, -sell_qty))


def trade_vev(
    od: OrderDepth,
    position: int,
    orders: list[Order],
) -> None:
    """Market-make VELVETFRUIT_EXTRACT around current mid."""
    m = mid(od)
    if m is None:
        m = VEV_FV_BASE

    pos = position

    # Take any obvious edge
    for ask_px, ask_vol in sorted(od.sell_orders.items()):
        if ask_px < m - VEV_HALF and pos < VEV_LIMIT:
            qty = min(-ask_vol, VEV_LIMIT - pos)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", ask_px, qty))
                pos += qty

    for bid_px, bid_vol in sorted(od.buy_orders.items(), reverse=True):
        if bid_px > m + VEV_HALF and pos > -VEV_LIMIT:
            qty = min(bid_vol, pos + VEV_LIMIT)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", bid_px, -qty))
                pos -= qty

    # Slight long bias (drift ~25/day), skew quotes
    skew = -position * 0.05
    buy_qty  = min(VEV_LIMIT - pos, 15)
    sell_qty = min(pos + VEV_LIMIT, 15)

    if buy_qty > 0:
        orders.append(Order("VELVETFRUIT_EXTRACT", math.floor(m + skew - VEV_HALF), buy_qty))
    if sell_qty > 0:
        orders.append(Order("VELVETFRUIT_EXTRACT", math.ceil(m + skew + VEV_HALF), -sell_qty))


def trade_voucher(
    symbol: str,
    strike: int,
    od: OrderDepth,
    position: int,
    S: float,
    T: float,
    orders: list[Order],
) -> None:
    """Trade a single voucher (call option)."""

    # Fair value
    if strike in DEEP_ITM_STRIKES:
        fv = max(0.0, S - strike)
    else:
        fv = bs_call(S, strike, T, SIGMA)

    thresh = TAKE_THRESH.get(strike, 1.5)
    half   = MM_HALF.get(strike, 1)
    limit  = VOUCHER_LIMIT

    pos = position

    # ── Taking phase ──────────────────────────────────────────────────────
    # Buy cheap asks
    for ask_px, ask_vol in sorted(od.sell_orders.items()):
        edge = fv - ask_px
        if edge >= thresh and pos < limit:
            qty = min(-ask_vol, limit - pos)
            if qty > 0:
                orders.append(Order(symbol, ask_px, qty))
                pos += qty

    # Sell expensive bids
    for bid_px, bid_vol in sorted(od.buy_orders.items(), reverse=True):
        edge = bid_px - fv
        if edge >= thresh and pos > -limit:
            qty = min(bid_vol, pos + limit)
            if qty > 0:
                orders.append(Order(symbol, bid_px, -qty))
                pos -= qty

    # ── Making phase ──────────────────────────────────────────────────────
    # Skew to manage inventory
    skew = -pos * 0.02

    bid_px = math.floor(fv + skew - half)
    ask_px = math.ceil(fv + skew + half)

    # Don't quote negative or zero prices
    if bid_px <= 0:
        bid_px = 1
    if ask_px <= bid_px:
        ask_px = bid_px + 1

    buy_qty  = min(limit - pos, 10)
    sell_qty = min(pos + limit, 10)

    if buy_qty > 0:
        orders.append(Order(symbol, bid_px, buy_qty))
    if sell_qty > 0:
        orders.append(Order(symbol, ask_px, -sell_qty))


# ── Main Trader ────────────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> tuple[dict[Symbol, list[Order]], int, str]:
        result: dict[Symbol, list[Order]] = {}

        # Load persisted state
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        # Current time-to-expiry
        T = tte(state.timestamp)

        # Get current VEV mid (use last known if not available)
        vev_od = state.order_depths.get("VELVETFRUIT_EXTRACT")
        if vev_od:
            S = mid(vev_od) or td.get("last_S", VEV_FV_BASE)
        else:
            S = td.get("last_S", VEV_FV_BASE)
        td["last_S"] = S

        # ── HYDROGEL_PACK ──────────────────────────────────────────────────
        if "HYDROGEL_PACK" in state.order_depths:
            orders: list[Order] = []
            pos = state.position.get("HYDROGEL_PACK", 0)
            trade_hydrogel(state.order_depths["HYDROGEL_PACK"], pos, orders)
            if orders:
                result["HYDROGEL_PACK"] = orders

        # ── VELVETFRUIT_EXTRACT ────────────────────────────────────────────
        if vev_od:
            orders = []
            pos = state.position.get("VELVETFRUIT_EXTRACT", 0)
            trade_vev(vev_od, pos, orders)
            if orders:
                result["VELVETFRUIT_EXTRACT"] = orders

        # ── Vouchers ──────────────────────────────────────────────────────
        for strike in VOUCHER_STRIKES:
            symbol = f"VEV_{strike}"
            if symbol not in state.order_depths:
                continue
            if strike in SKIP_STRIKES:
                continue
            orders = []
            pos = state.position.get(symbol, 0)
            trade_voucher(
                symbol, strike,
                state.order_depths[symbol],
                pos, S, T, orders,
            )
            if orders:
                result[symbol] = orders

        # Persist state
        trader_data = json.dumps(td)

        return result, 0, trader_data
