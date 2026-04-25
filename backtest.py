"""
Backtester for Round 3 trader using historical price CSVs.

Fill model (mirrors Prosperity rules):
  - Our BUY  order at price P fills against market asks with price <= P
  - Our SELL order at price P fills against market bids with price >= P
  - Volume filled = min(our qty, available market volume at that level)

P&L:
  - Realized: cash flow from every fill (buy = -cash, sell = +cash)
  - Mark-to-market: open position * mid at end of each day
  - Total = realized + unrealized (mark-to-market)
"""

import csv
import json
import math
from collections import defaultdict
from datamodel import Order, OrderDepth, TradingState
from trader import Trader


# ── Load historical data ───────────────────────────────────────────────────────

def load_prices(day: int) -> dict[int, dict[str, dict]]:
    """Returns {timestamp: {product: row_dict}}"""
    path = f"ROUND_3/prices_round_3_day_{day}.csv"
    data: dict[int, dict[str, dict]] = defaultdict(dict)
    with open(path) as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            ts  = int(row["timestamp"])
            prd = row["product"]
            data[ts][prd] = row
    return data


def row_to_orderbook(row: dict) -> OrderDepth:
    od = OrderDepth()
    for i in ["1", "2", "3"]:
        bp = row.get(f"bid_price_{i}", "")
        bv = row.get(f"bid_volume_{i}", "")
        ap = row.get(f"ask_price_{i}", "")
        av = row.get(f"ask_volume_{i}", "")
        if bp and bv:
            od.buy_orders[int(float(bp))] = int(float(bv))
        if ap and av:
            od.sell_orders[int(float(ap))] = -int(float(av))  # negative convention
    return od


# ── Fill simulation ────────────────────────────────────────────────────────────

def simulate_fills(
    orders: list[Order],
    od: OrderDepth,
    position: int,
    limit: int,
) -> tuple[list[tuple[int, int]], int]:
    """
    Returns (fills, new_position).
    fills = list of (price, qty) — qty positive=buy, negative=sell.
    Respects position limit: skips orders that would breach it.
    """
    fills: list[tuple[int, int]] = []
    pos = position

    # Sort buy orders (highest price first — most aggressive)
    buy_orders  = sorted([o for o in orders if o.quantity > 0], key=lambda o: -o.price)
    sell_orders = sorted([o for o in orders if o.quantity < 0], key=lambda o:  o.price)

    # Available market asks (sorted lowest first)
    market_asks = sorted(od.sell_orders.items())   # (price, neg_vol)
    market_bids = sorted(od.buy_orders.items(), reverse=True)  # (price, pos_vol)

    ask_avail = {p: -v for p, v in market_asks}   # price -> available qty
    bid_avail = {p:  v for p, v in market_bids}

    for o in buy_orders:
        if pos >= limit:
            break
        for ask_px in sorted(ask_avail):
            if ask_px > o.price:
                break
            can_buy = min(o.quantity, ask_avail[ask_px], limit - pos)
            if can_buy <= 0:
                continue
            fills.append((ask_px, can_buy))
            pos += can_buy
            ask_avail[ask_px] -= can_buy
            o = Order(o.symbol, o.price, o.quantity - can_buy)
            if o.quantity <= 0:
                break

    for o in sell_orders:
        if pos <= -limit:
            break
        for bid_px in sorted(bid_avail, reverse=True):
            if bid_px < o.price:
                break
            can_sell = min(-o.quantity, bid_avail[bid_px], pos + limit)
            if can_sell <= 0:
                continue
            fills.append((bid_px, -can_sell))
            pos -= can_sell
            bid_avail[bid_px] -= can_sell
            o = Order(o.symbol, o.price, o.quantity + can_sell)
            if o.quantity >= 0:
                break

    return fills, pos


# ── Main backtest loop ─────────────────────────────────────────────────────────

LIMITS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    **{f"VEV_{k}": 300 for k in [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500]},
}


def run_backtest(days: list[int] = [0, 1, 2], verbose: bool = False):
    trader = Trader()
    trader_data = ""

    positions: dict[str, int] = defaultdict(int)
    cash: dict[str, float] = defaultdict(float)  # per-product cash (realized)
    total_fills = defaultdict(int)
    pnl_history: list[tuple[int, int, float]] = []  # (day, ts, total_pnl)

    for day in days:
        prices = load_prices(day)
        timestamps = sorted(prices.keys())

        for ts in timestamps:
            tick_data = prices[ts]

            # Build state
            order_depths = {}
            for prd, row in tick_data.items():
                order_depths[prd] = row_to_orderbook(row)

            # Adjust timestamp to be cumulative across days
            # Prosperity timestamps restart each day; we keep them per-day here
            state = TradingState(
                traderData=trader_data,
                timestamp=ts,   # within-day timestamp (0..999900)
                order_depths=order_depths,
                position=dict(positions),
            )

            # Run trader
            try:
                result, _, trader_data = trader.run(state)
            except Exception as e:
                print(f"  ERROR at day={day} ts={ts}: {e}")
                continue

            # Simulate fills per product
            for symbol, orders in result.items():
                if symbol not in order_depths:
                    continue
                od = order_depths[symbol]
                limit = LIMITS.get(symbol, 200)
                fills, new_pos = simulate_fills(
                    orders, od, positions[symbol], limit
                )
                positions[symbol] = new_pos
                for price, qty in fills:
                    cash[symbol] -= price * qty  # buy=cash out, sell=cash in
                    total_fills[symbol] += abs(qty)

            # Compute total P&L at this tick (realized + unrealized mark-to-market)
            total_pnl = sum(cash.values())
            for prd, row in tick_data.items():
                pos = positions[prd]
                if pos != 0 and row.get("mid_price"):
                    total_pnl += pos * float(row["mid_price"])

            pnl_history.append((day, ts, total_pnl))

            if verbose and ts % 10000 == 0:
                print(f"  day={day} ts={ts:>7}  pnl={total_pnl:>10.0f}  "
                      f"pos_VEV={positions.get('VELVETFRUIT_EXTRACT',0):>4}  "
                      f"pos_HYD={positions.get('HYDROGEL_PACK',0):>4}")

        # End-of-day summary
        day_pnl = pnl_history[-1][2] if pnl_history else 0
        print(f"\nEnd of day {day}:  PnL = {day_pnl:>10,.0f}")
        for prd in sorted(positions):
            if positions[prd] != 0:
                print(f"  {prd:<28} pos={positions[prd]:>5}")

    # ── Final report ──────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("FINAL BACKTEST SUMMARY")
    print("═" * 60)

    final_pnl = pnl_history[-1][2] if pnl_history else 0
    print(f"Total PnL            : {final_pnl:>12,.2f}")

    print("\nFills per product:")
    for prd in sorted(total_fills):
        print(f"  {prd:<28} {total_fills[prd]:>6} units traded")

    print("\nFinal positions:")
    for prd in sorted(positions):
        pos = positions[prd]
        if pos != 0:
            print(f"  {prd:<28} {pos:>5}")

    # Per-product realized P&L
    print("\nPer-product realized cash (excl. open positions):")
    for prd in sorted(cash):
        if cash[prd] != 0:
            print(f"  {prd:<28} {cash[prd]:>10,.2f}")

    # P&L breakdown by day
    print("\nPnL progression (sampled every 10000 ticks):")
    prev = 0.0
    for day, ts, pnl in pnl_history:
        if ts % 10000 == 0:
            delta = pnl - prev
            print(f"  day={day} ts={ts:>7}  cumulative={pnl:>10,.0f}  "
                  f"delta={delta:>+8,.0f}")
            prev = pnl

    return pnl_history, positions, cash


if __name__ == "__main__":
    import sys
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    run_backtest(days=[0, 1, 2], verbose=verbose)
