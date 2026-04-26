"""
Backtester for Round 3 using historical price CSVs.

Key differences from live round:
  Historical CSV : timestamps 0 → 999,900  (10,000 ticks/day, step 100)
  Live round     : timestamps 0 →  99,900  ( 1,000 ticks/round, step 100)

Historical days and their starting TTE:
  Day 0 (tutorial) : TTE = 8 days
  Day 1 (round 1)  : TTE = 7 days
  Day 2 (round 2)  : TTE = 6 days
  Live round 3     : TTE = 5 days  ← what trader.py is tuned for

Fill model: resting-order price improvement (standard limit order book).
  Our BUY  at P fills against market asks ≤ P, at the market ask price.
  Our SELL at P fills against market bids ≥ P, at the market bid price.
"""

import csv, json, math
from collections import defaultdict
from datamodel import Order, OrderDepth, TradingState
from trader import Trader, bs_call, bs_delta, SIGMA

# Historical data has 10x more ts-units per day than the live round
HIST_TPD = 1_000_000   # timestamp-units per historical day (10,000 ticks × 100)
TTE_BY_DAY = {0: 8, 1: 7, 2: 6}

LIMITS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    **{f"VEV_{k}": 300 for k in [4000,4500,5000,5100,5200,5300,5400,5500,6000,6500]},
}


def load_prices(day: int) -> dict[int, dict[str, dict]]:
    data: dict[int, dict[str, dict]] = defaultdict(dict)
    with open(f"ROUND_3/prices_round_3_day_{day}.csv") as f:
        for row in csv.DictReader(f, delimiter=";"):
            data[int(row["timestamp"])][row["product"]] = row
    return data


def row_to_od(row: dict) -> OrderDepth:
    od = OrderDepth()
    for i in ("1", "2", "3"):
        bp, bv = row.get(f"bid_price_{i}"), row.get(f"bid_volume_{i}")
        ap, av = row.get(f"ask_price_{i}"), row.get(f"ask_volume_{i}")
        if bp and bv:
            od.buy_orders[int(float(bp))]  =  int(float(bv))
        if ap and av:
            od.sell_orders[int(float(ap))] = -int(float(av))
    return od


def simulate_fills(orders, od, position, limit):
    fills, pos = [], position
    asks = {p: -v for p, v in od.sell_orders.items()}
    bids = {p:  v for p, v in  od.buy_orders.items()}

    for o in sorted([x for x in orders if x.quantity > 0], key=lambda x: -x.price):
        for px in sorted(asks):
            if px > o.price or pos >= limit:
                break
            qty = min(o.quantity, asks[px], limit - pos)
            if qty > 0:
                fills.append((px, qty))
                pos += qty; asks[px] -= qty; o = Order(o.symbol, o.price, o.quantity - qty)
            if o.quantity <= 0:
                break

    for o in sorted([x for x in orders if x.quantity < 0], key=lambda x: x.price):
        for px in sorted(bids, reverse=True):
            if px < o.price or pos <= -limit:
                break
            qty = min(-o.quantity, bids[px], pos + limit)
            if qty > 0:
                fills.append((px, -qty))
                pos -= qty; bids[px] -= qty; o = Order(o.symbol, o.price, o.quantity + qty)
            if o.quantity >= 0:
                break

    return fills, pos


def run_backtest(days=(0, 1, 2)):
    trader      = Trader()
    trader_data = ""
    positions   = defaultdict(int)
    cash        = defaultdict(float)
    fills_count = defaultdict(int)
    pnl_log     = []

    for day in days:
        prices    = load_prices(day)
        tte_base  = TTE_BY_DAY[day]
        timestamps = sorted(prices)

        # Inject correct TTE and historical timestamp scale into traderData
        # The trader reads these via td["tte_base"] and td["tpd"]
        try:
            td_dict = json.loads(trader_data) if trader_data else {}
        except Exception:
            td_dict = {}
        td_dict["tte_base"] = tte_base
        td_dict["tpd"]      = HIST_TPD
        trader_data = json.dumps(td_dict)

        for ts in timestamps:
            tick = prices[ts]
            ods  = {p: row_to_od(r) for p, r in tick.items()}

            state = TradingState(
                traderData   = trader_data,
                timestamp    = ts,
                order_depths = ods,
                position     = dict(positions),
            )

            try:
                orders_map, _, trader_data = trader.run(state)
            except Exception as e:
                print(f"  ERROR day={day} ts={ts}: {e}")
                # Re-inject overrides in case traderData was reset
                try:
                    td_dict = json.loads(trader_data) if trader_data else {}
                except Exception:
                    td_dict = {}
                td_dict["tte_base"] = tte_base
                td_dict["tpd"]      = HIST_TPD
                trader_data = json.dumps(td_dict)
                continue

            # Keep tte_base/tpd in traderData after every tick
            try:
                td_dict = json.loads(trader_data)
            except Exception:
                td_dict = {}
            td_dict["tte_base"] = tte_base
            td_dict["tpd"]      = HIST_TPD
            trader_data = json.dumps(td_dict)

            for sym, orders in orders_map.items():
                if sym not in ods:
                    continue
                fills, new_pos = simulate_fills(
                    orders, ods[sym], positions[sym], LIMITS.get(sym, 200))
                positions[sym] = new_pos
                for px, qty in fills:
                    cash[sym]       -= px * qty
                    fills_count[sym] += abs(qty)

            # Mark-to-market PnL
            mtm = sum(cash.values())
            for p, row in tick.items():
                pos = positions[p]
                if pos and row.get("mid_price"):
                    mtm += pos * float(row["mid_price"])
            pnl_log.append((day, ts, mtm))

        day_pnl = pnl_log[-1][2] if pnl_log else 0
        print(f"\nEnd of day {day}  (TTE started at {tte_base}d):  PnL = {day_pnl:>10,.0f}")
        for p in sorted(positions):
            if positions[p]:
                print(f"  {p:<28} pos={positions[p]:>5}")

    # Summary
    print("\n" + "═"*60)
    final = pnl_log[-1][2] if pnl_log else 0
    print(f"TOTAL PnL  :  {final:>12,.2f}")
    print("\nFills:")
    for p in sorted(fills_count):
        print(f"  {p:<28} {fills_count[p]:>6} units")
    print("\nPer-product realized cash:")
    for p in sorted(cash):
        if cash[p]:
            print(f"  {p:<28} {cash[p]:>12,.2f}")

    print("\nPnL over time (every 100k ts):")
    prev = 0.0
    for day, ts, pnl in pnl_log:
        if ts % 100_000 == 0:
            print(f"  day={day} ts={ts:>7}  cum={pnl:>10,.0f}  Δ={pnl-prev:>+9,.0f}")
            prev = pnl

    return pnl_log, positions, cash


if __name__ == "__main__":
    run_backtest()
