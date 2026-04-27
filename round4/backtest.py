"""
Backtester for round4/trader.py strategy on historical prices_round_4_day_*.csv
Run: python3 backtest.py
"""
import csv, math, json
from collections import defaultdict

# ── BS ────────────────────────────────────────────────────────────────────────
def nc(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S,K,T,sig=0.013):
    if T<=0 or sig<=0: return max(0.0,S-K)
    sv=sig*math.sqrt(T); d1=(math.log(S/K)+.5*sig**2*T)/sv
    return S*nc(d1)-K*nc(d1-sv)

# ── Load data ─────────────────────────────────────────────────────────────────
def load(day):
    rows = defaultdict(list)
    with open(f'ROUND_4/prices_round_4_day_{day}.csv') as f:
        for r in csv.DictReader(f, delimiter=';'):
            prod = r['product']
            ts   = int(r['timestamp'])
            try:
                bids = sorted([(float(r[f'bid_price_{i}']), int(r[f'bid_volume_{i}']))
                               for i in range(1,4) if r.get(f'bid_price_{i}') and r[f'bid_price_{i}']], reverse=True)
                asks = sorted([(float(r[f'ask_price_{i}']), int(r[f'ask_volume_{i}']))
                               for i in range(1,4) if r.get(f'ask_price_{i}') and r[f'ask_price_{i}']])
                mid  = float(r['mid_price']) if r.get('mid_price') else None
                rows[prod].append({'ts':ts,'bids':bids,'asks':asks,'mid':mid})
            except: pass
    return rows

# ── Strategy params ───────────────────────────────────────────────────────────
FV        = 9991
HALF      = 7
H_LIMIT   = 200
H_PASS    = 30

VEV_HALF  = 2
VEV_LIMIT = 200
VEV_PASS  = 10

SHORT_K   = {5300:300, 5400:300, 5500:300}
S_THRESH  = 50
BUY_K     = {5000:300, 5100:300, 5200:300}
B_THRESH  = 50
DIP_TRIG  = 50   # buy ITM if VEV < start - 50
OPT_LIMIT = 300

TTE_BASE  = {'day1':7.0,'day2':6.0,'day3':5.0}  # T at ts=0 each day
TPD       = 1_000_000

# ── Simulate one day ──────────────────────────────────────────────────────────
def sim_day(day_key, tte_start):
    data = load(int(day_key[-1]))
    all_ts = sorted(set(r['ts'] for v in data.values() for r in v))

    # index by product→ts
    idx = {}
    for prod, rows in data.items():
        idx[prod] = {r['ts']: r for r in rows}

    pos   = defaultdict(int)   # product → position
    cash  = 0.0
    vev_start = None

    def get(prod, ts):
        return idx.get(prod,{}).get(ts)

    for ts in all_ts:
        T = max(tte_start - ts/TPD, 1e-4)

        # VEV mid
        vr = get('VELVETFRUIT_EXTRACT', ts)
        S  = vr['mid'] if vr and vr['mid'] else None
        if S and vev_start is None:
            vev_start = S

        # ── HYDROGEL ─────────────────────────────────────────────────────────
        hr = get('HYDROGEL_PACK', ts)
        if hr:
            # take
            for bid_px, bvol in hr['bids']:
                if bid_px <= FV+HALF or pos['HYD'] <= -H_LIMIT: break
                qty = min(bvol, pos['HYD']+H_LIMIT)
                cash += bid_px*qty; pos['HYD'] -= qty
            for ask_px, avol in hr['asks']:
                if ask_px >= FV-HALF or pos['HYD'] >= H_LIMIT: break
                qty = min(avol, H_LIMIT-pos['HYD'])
                cash -= ask_px*qty; pos['HYD'] += qty
            # passive (approximated: filled if market at our level)
            mid_h = hr['mid'] or FV
            if hr['bids'] and hr['bids'][0][0] >= FV+HALF+1 and pos['HYD'] > -H_LIMIT:
                qty = min(H_PASS, pos['HYD']+H_LIMIT)
                cash += (FV+HALF+1)*qty; pos['HYD'] -= qty
            if hr['asks'] and hr['asks'][0][0] <= FV-HALF and pos['HYD'] < H_LIMIT:
                qty = min(H_PASS, H_LIMIT-pos['HYD'])
                cash -= (FV-HALF)*qty; pos['HYD'] += qty

        # ── VEV MM ────────────────────────────────────────────────────────────
        if vr and S:
            skew = max(-VEV_HALF, min(VEV_HALF, -pos['VEV']*0.05))
            bid_p = int(S+skew-VEV_HALF); ask_p = int(S+skew+VEV_HALF)
            for ask_px, avol in vr['asks']:
                if ask_px >= S-VEV_HALF or pos['VEV'] >= VEV_LIMIT: break
                qty = min(avol, VEV_LIMIT-pos['VEV'])
                cash -= ask_px*qty; pos['VEV'] += qty
            for bid_px, bvol in vr['bids']:
                if bid_px <= S+VEV_HALF or pos['VEV'] <= -VEV_LIMIT: break
                qty = min(bvol, pos['VEV']+VEV_LIMIT)
                cash += bid_px*qty; pos['VEV'] -= qty
            # passive
            if vr['bids'] and vr['bids'][0][0] >= ask_p and pos['VEV'] > -VEV_LIMIT:
                qty = min(VEV_PASS, pos['VEV']+VEV_LIMIT)
                cash += ask_p*qty; pos['VEV'] -= qty
            if vr['asks'] and vr['asks'][0][0] <= bid_p and pos['VEV'] < VEV_LIMIT:
                qty = min(VEV_PASS, VEV_LIMIT-pos['VEV'])
                cash -= bid_p*qty; pos['VEV'] += qty

        # ── Options ───────────────────────────────────────────────────────────
        if S:
            dip_mode = vev_start and S < vev_start - DIP_TRIG

            if dip_mode:
                for K, cap in BUY_K.items():
                    sym = f'VEV_{K}'
                    or_ = get(sym, ts)
                    if not or_: continue
                    fv_opt = bs(S, K, T)
                    for ask_px, avol in or_['asks']:
                        if ask_px > fv_opt+B_THRESH or pos[sym] >= cap: break
                        qty = min(avol, cap-pos[sym])
                        cash -= ask_px*qty; pos[sym] += qty
            else:
                for K, cap in SHORT_K.items():
                    sym = f'VEV_{K}'
                    or_ = get(sym, ts)
                    if not or_: continue
                    fv_opt = bs(S, K, T)
                    for bid_px, bvol in or_['bids']:
                        if fv_opt-bid_px > S_THRESH or pos[sym] <= -cap: break
                        qty = min(bvol, pos[sym]+cap)
                        cash += bid_px*qty; pos[sym] -= qty

    # Mark-to-market all positions at end
    last_ts = all_ts[-1]
    T_end   = max(tte_start - last_ts/TPD, 1e-4)
    vr_end  = get('VELVETFRUIT_EXTRACT', last_ts)
    S_end   = vr_end['mid'] if vr_end and vr_end['mid'] else vev_start or 5232

    mtm = cash
    for prod, p in pos.items():
        if p == 0: continue
        if prod == 'HYD':
            hr_end = get('HYDROGEL_PACK', last_ts)
            px = hr_end['mid'] if hr_end and hr_end['mid'] else FV
        elif prod == 'VEV':
            px = S_end
        elif prod.startswith('VEV_'):
            K = int(prod[4:])
            px = bs(S_end, K, T_end)
        else:
            continue
        mtm += p * px

    return mtm, dict(pos), S_end

# ── Run all 3 days ────────────────────────────────────────────────────────────
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print(f"{'Day':<6} {'MTM PnL':>12}  End positions (non-zero)")
grand = 0
for day, tte_s in [('day1',7.0),('day2',6.0),('day3',5.0)]:
    mtm, positions, S_end = sim_day(day, tte_s)
    grand += mtm
    nonzero = {k:v for k,v in positions.items() if v!=0}
    print(f"{day:<6} {mtm:>12,.0f}  {nonzero}  VEV_end={S_end:.0f}")

print(f"\n{'3-day total':>6} {grand:>12,.0f}")
print(f"Avg per day: {grand/3:,.0f}")
print(f"\nNote: Day3 = round3 live path. Round4 will have its own path.")
