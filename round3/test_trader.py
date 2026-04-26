"""
Quick smoke-test: run one tick for each product and print orders.
"""

from datamodel import OrderDepth, TradingState
from trader import Trader, bs_call, tte, SIGMA


def make_book(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> OrderDepth:
    od = OrderDepth()
    for px, vol in bids:
        od.buy_orders[px] = vol
    for px, vol in asks:
        od.sell_orders[px] = -vol  # negative convention
    return od


def test_bs():
    # IV sanity check
    S, T = 5250.0, 5 / 365
    for K, expected_mkt in [(5200, 101.5), (5300, 53.0), (5400, 23.0), (5500, 8.5)]:
        fv = bs_call(S, K, T, SIGMA)
        print(f"  BS({K}): {fv:.2f}  (historical mid ~{expected_mkt})")


def test_tte():
    for ts in [0, 5000, 9900, 10000, 49900]:
        print(f"  ts={ts}: TTE={tte(ts)*365:.4f} days ({tte(ts):.6f} yrs)")


def test_trader_tick():
    state = TradingState(
        traderData="",
        timestamp=0,
        order_depths={
            "HYDROGEL_PACK": make_book([(9985, 10), (9980, 20)], [(10001, 10), (10005, 20)]),
            "VELVETFRUIT_EXTRACT": make_book([(5248, 15)], [(5253, 15)]),
            "VEV_5200": make_book([(100, 10)], [(103, 10)]),
            "VEV_5300": make_book([(51, 10)], [(55, 10)]),
            "VEV_5400": make_book([(21, 10)], [(25, 10)]),
            "VEV_5500": make_book([(7, 10)], [(10, 10)]),
            "VEV_5000": make_book([(253, 10)], [(260, 10)]),
            "VEV_5100": make_book([(162, 10)], [(172, 10)]),
            "VEV_4000": make_book([(1248, 10)], [(1252, 10)]),
            "VEV_4500": make_book([(748, 10)], [(752, 10)]),
        },
        position={},
    )

    trader = Trader()
    orders, conversions, td = trader.run(state)

    print("\n=== Orders ===")
    for sym, ords in sorted(orders.items()):
        for o in ords:
            print(f"  {o}")
    print(f"\nTraderData: {td}")
    print(f"Conversions: {conversions}")

    # Verify no position limit violations
    buys = {}
    sells = {}
    for sym, ords in orders.items():
        for o in ords:
            if o.quantity > 0:
                buys[sym] = buys.get(sym, 0) + o.quantity
            else:
                sells[sym] = sells.get(sym, 0) + abs(o.quantity)

    limits = {"HYDROGEL_PACK": 200, "VELVETFRUIT_EXTRACT": 200}
    for k in [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500]:
        limits[f"VEV_{k}"] = 300

    print("\n=== Position checks ===")
    for sym, lim in limits.items():
        b = buys.get(sym, 0)
        s = sells.get(sym, 0)
        print(f"  {sym}: buy={b} sell={s} (limit={lim}) {'OK' if b <= lim and s <= lim else 'VIOLATION'}")


if __name__ == "__main__":
    print("=== BS sanity ===")
    test_bs()
    print("\n=== TTE sanity ===")
    test_tte()
    test_trader_tick()
