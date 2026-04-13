#!/usr/bin/env python3
"""
ScalpingEURUSD – Fast 2-Phase Optimizer
Goal: WR > 70%, RR 1:1 (SL = TP)

Phase 1: Rough scan ~1200 combos (~2 min) → find best region
Phase 2: Fine tune ~500 combos (~1 min) around best region
"""
import warnings; warnings.filterwarnings('ignore')
import sys, time, itertools
import pandas as pd, numpy as np

POINT    = 0.00001
CONTRACT = 100_000.0

# ─────────────────────────────────────────────────────────────
def load_data(path):
    df = pd.read_csv(path)
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
    else:
        df.columns = [c.lower() for c in df.columns]
        df['datetime'] = pd.to_datetime(df.iloc[:,0])
    df = df[['datetime','open','high','low','close']].dropna()
    return df.sort_values('datetime').reset_index(drop=True)

def compute_indicators(df):
    c = df['close']
    df['e21']  = c.ewm(span=21, adjust=False).mean()
    df['e50']  = c.ewm(span=50, adjust=False).mean()
    df['e21p'] = df['e21'].shift(1)
    df['e50p'] = df['e50'].shift(1)
    df['cp']   = c.shift(1)
    delta = c.diff()
    ag = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al = (-delta).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    df['rsi']  = (100 - 100/(1+rs)).fillna(50.0)
    df['body'] = (df['close'] - df['open']).abs()
    return df

def build_arrays(df, server_gmt=3):
    vn_offset = pd.Timedelta(hours=(7 - server_gmt))
    vn_dt  = df['datetime'] + vn_offset
    return dict(
        e21     = df['e21'].values,
        e50     = df['e50'].values,
        e21p    = df['e21p'].values,
        e50p    = df['e50p'].values,
        cp      = df['cp'].values,
        rsi     = df['rsi'].values,
        hi      = df['high'].values,
        lo      = df['low'].values,
        cl      = df['close'].values,
        op      = df['open'].values,
        body    = df['body'].values,
        vn_h    = vn_dt.dt.hour.values.astype(np.int8),
        weekday = vn_dt.dt.weekday.values.astype(np.int8),
        dates   = vn_dt.dt.date.values,
    )

def make_sess(arr, s_start, s_end):
    return (arr['vn_h'] >= s_start) & (arr['vn_h'] < s_end)

# ─────────────────────────────────────────────────────────────
# SIGNAL DETECTION (vectorized)
# ─────────────────────────────────────────────────────────────
def detect_signals(arr, sess, p):
    buf   = p['ema_buf'] * POINT
    mbody = p['min_body'] * POINT
    pull  = arr['e50'] if p['ema50_touch'] else arr['e21']

    buy  = ((arr['e21'] > arr['e50']) & (arr['e21p'] > arr['e50p']) &
            (arr['lo'] <= pull + buf) &
            (arr['cl'] > arr['e21']) & (arr['cl'] > arr['op']) &
            (arr['rsi'] >= p['rsi_buy_min']) & (arr['rsi'] <= p['rsi_buy_max']) &
            (arr['cp'] > arr['e50p']) &
            (arr['body'] >= mbody) & sess)

    sell = ((arr['e21'] < arr['e50']) & (arr['e21p'] < arr['e50p']) &
            (arr['hi'] >= pull - buf) &
            (arr['cl'] < arr['e21']) & (arr['cl'] < arr['op']) &
            (arr['rsi'] >= p['rsi_sell_min']) & (arr['rsi'] <= p['rsi_sell_max']) &
            (arr['cp'] < arr['e50p']) &
            (arr['body'] >= mbody) & sess)

    sig = np.zeros(len(arr['cl']), dtype=np.int8)
    sig[buy]  = 1
    sig[sell] = -1
    return sig

# ─────────────────────────────────────────────────────────────
# FAST SIMULATION (jump-to-exit per trade)
# ─────────────────────────────────────────────────────────────
def simulate(arr, sess, sigs, p, init_bal=100.0):
    hi=arr['hi']; lo=arr['lo']; cl=arr['cl']; op=arr['op']
    vn_h=arr['vn_h']; wd=arr['weekday']; dates=arr['dates']
    n = len(hi)

    sl_p = p['sl_tp'] * POINT
    tp_p = p['sl_tp'] * POINT          # RR 1:1
    be_a = p['sl_tp'] * 0.5 * POINT    # BE at 50% SL
    be_l = max(3, p['sl_tp'] * 0.1) * POINT
    sprd = 6 * POINT
    s_end = p['sess_end']
    risk  = p.get('risk_pct', 1.0)
    max_cl= p.get('max_consec', 2)
    max_dd= p.get('max_dd', 3.0)

    trades=[]; bal=init_bal; consec=0
    day_date=None; day_loss=0.0; day_start=bal
    i = 1

    while i < n - 1:
        d = dates[i]
        if d != day_date:
            day_date=d; day_loss=0.0; day_start=bal; consec=0

        if (day_start>0 and day_loss/day_start*100>=max_dd) or consec>=max_cl:
            i+=1; continue
        if sigs[i]==0:
            i+=1; continue

        sig = int(sigs[i])
        entry = op[i+1] + (sprd if sig==1 else 0.0)
        sl_price = entry - sl_p*sig
        tp_price = entry + tp_p*sig
        lot = max(0.01, round(bal*risk/100/(p['sl_tp']*10)/0.01)*0.01)
        lot = min(lot, 100.0)

        cur_sl = sl_price; be_done = False
        exit_px = 0.0; reason = ''; j = i+1

        while j < n:
            # BE trigger
            if not be_done:
                if sig==1 and hi[j] >= entry+be_a:
                    new_sl = entry+be_l
                    if new_sl > cur_sl: cur_sl=new_sl; be_done=True
                elif sig==-1 and lo[j] <= entry-be_a:
                    new_sl = entry-be_l
                    if new_sl < cur_sl: cur_sl=new_sl; be_done=True

            if sig==1:
                if lo[j]<=cur_sl: exit_px=cur_sl; reason='BE' if be_done else 'SL'; break
                if hi[j]>=tp_price: exit_px=tp_price; reason='TP'; break
            else:
                if hi[j]>=cur_sl: exit_px=cur_sl; reason='BE' if be_done else 'SL'; break
                if lo[j]<=tp_price: exit_px=tp_price; reason='TP'; break

            h = int(vn_h[j])
            if h >= s_end-1:
                nxt = sess[j+1] if j+1<n else False
                if not nxt: exit_px=cl[j]; reason='EOD'; break
            if wd[j]==4 and h>=21: exit_px=cl[j]; reason='EOD'; break
            j+=1

        if exit_px==0.0: i=j+1; continue

        pnl = round((exit_px-entry)*sig*lot*CONTRACT, 2)
        trades.append({'pnl':pnl,'reason':reason,'dir':sig,'lots':lot,
                       'entry':entry,'exit':exit_px})
        bal += pnl
        if pnl<=0: day_loss+=abs(pnl); consec+=1
        else: consec=0
        i = j+1

    return trades

def calc_stats(trades, init_bal=100.0):
    if not trades: return dict(n=0,wins=0,wr=0,pnl=0,dd=0,pf=0,tp=0,sl=0,be=0,eod=0)
    n=len(trades); wins=sum(1 for t in trades if t['pnl']>0)
    pnl=sum(t['pnl'] for t in trades)
    gw=sum(t['pnl'] for t in trades if t['pnl']>0)
    gl=abs(sum(t['pnl'] for t in trades if t['pnl']<=0))
    pf = gw/gl if gl>0 else 99.0
    bal=init_bal; peak=bal; mdd=0.0
    for t in trades:
        bal+=t['pnl']; peak=max(peak,bal)
        mdd=max(mdd,(peak-bal)/peak*100 if peak>0 else 0)
    rs={};
    for t in trades: rs[t['reason']]=rs.get(t['reason'],0)+1
    return dict(n=n,wins=wins,wr=wins/n*100,pnl=pnl,dd=mdd,pf=pf,
                tp=rs.get('TP',0),sl=rs.get('SL',0),be=rs.get('BE',0),eod=rs.get('EOD',0))

# ─────────────────────────────────────────────────────────────
# GRID DEFINITIONS
# ─────────────────────────────────────────────────────────────
GRID_PHASE1 = {
    'sl_tp':          [25, 30, 40, 50],
    'rsi_buy_min':    [48, 52, 55],
    'rsi_buy_max':    [62, 65, 68],
    'rsi_sell_min':   [30, 33, 35],
    'rsi_sell_max':   [50, 52, 55],
    'ema_buf':        [20, 30, 40],
    'ema50_touch':    [True, False],
    'min_body':       [0, 8, 15],
    'sess_start':     [14, 15, 16],
    'sess_end':       [19, 20, 21, 22],
}
# 4×3×3×3×3×3×2×3×3×4 = 69,984  → still too large

# Phase 1: Only key parameters
GRID_P1 = {
    'sl_tp':       [25, 30, 40, 50, 60],
    'ema50_touch': [True, False],
    'min_body':    [0, 8, 15],
    'sess_start':  [14, 15, 16],
    'sess_end':    [19, 20, 21, 22],
    # RSI and buffer fixed at best guesses
}
P1_FIXED = dict(rsi_buy_min=50, rsi_buy_max=65, rsi_sell_min=30, rsi_sell_max=52, ema_buf=30)

# Phase 2: Fix best sl/sess/ema50, vary RSI + buffer
GRID_P2 = {
    'rsi_buy_min':  [45, 48, 50, 52, 55, 57],
    'rsi_buy_max':  [60, 63, 65, 68, 70],
    'rsi_sell_min': [28, 30, 33, 35, 38],
    'rsi_sell_max': [48, 50, 52, 55, 58],
    'ema_buf':      [15, 20, 25, 30, 35, 45],
    'min_body':     [0, 5, 8, 12, 15, 20],
}

def run_grid(arr, grid_params, fixed_params, label, min_wr=60.0, min_n=20):
    keys = list(grid_params.keys())
    vals = [grid_params[k] for k in keys]
    total = 1
    for v in vals: total *= len(v)

    print(f'\n{label}: {total:,} combinations...')
    results = []; t0 = time.time()

    # Pre-build session arrays for each unique (sess_start, sess_end)
    sess_cache = {}

    for idx, combo in enumerate(itertools.product(*vals)):
        p = {**fixed_params, **dict(zip(keys, combo))}

        ss = p.get('sess_start', fixed_params.get('sess_start', 14))
        se = p.get('sess_end',   fixed_params.get('sess_end',   22))
        sk = (ss, se)
        if sk not in sess_cache:
            sess_cache[sk] = make_sess(arr, ss, se)
        sess = sess_cache[sk]

        sigs   = detect_signals(arr, sess, p)
        trades = simulate(arr, sess, sigs, p)
        s      = calc_stats(trades)

        if s['n'] >= min_n and s['wr'] >= min_wr:
            results.append({**p, **s})

        if (idx+1) % 100 == 0:
            el  = time.time()-t0
            eta = (total-idx-1)/((idx+1)/el) if el>0 else 0
            best= max((r['wr'] for r in results), default=0)
            print(f'  [{idx+1:5d}/{total}]  {len(results):3d} pass  ETA {eta:.0f}s  bestWR={best:.1f}%')

    t = time.time()-t0
    print(f'  Done {t:.1f}s  =>  {len(results)} configs WR>={min_wr}%')
    results.sort(key=lambda r: (-r['wr'], -r['pnl']))
    return results

def print_top(results, n=20, label=''):
    if not results:
        print('  No results.'); return
    if label: print(f'\n{"="*90}\n  {label}\n{"="*90}')
    hdr = f"{'#':>3}  {'SL/TP':>5}  {'RSIbuy':>8}  {'RSIsell':>8}  {'buf':>3}  {'E50?':>4}  {'Body':>4}  {'Sess':>7}  {'N':>4}  {'WR%':>6}  {'P&L':>7}  {'DD%':>5}  {'PF':>5}"
    print(hdr); print('-'*90)
    for i,r in enumerate(results[:n],1):
        rb = f"{r['rsi_buy_min']}-{r['rsi_buy_max']}"
        rs = f"{r['rsi_sell_min']}-{r['rsi_sell_max']}"
        se = f"{r['sess_start']}-{r['sess_end']}VN"
        e5 = 'Y' if r['ema50_touch'] else 'n'
        print(f"{i:>3}  {r['sl_tp']:>3}pts  {rb:>8}  {rs:>8}  {r['ema_buf']:>3}  {e5:>4}  {r['min_body']:>3}p  {se:>7}  "
              f"{r['n']:>4}  {r['wr']:>5.1f}%  ${r['pnl']:>6.2f}  {r['dd']:>5.1f}%  {r['pf']:>5.2f}")

def print_best(r):
    print(f'\n{"="*60}')
    print(f'  OPTIMAL CONFIG  WR={r["wr"]:.1f}%  P&L=${r["pnl"]:.2f}')
    print(f'{"="*60}')
    print(f'  SL = TP          : {r["sl_tp"]} pts  ({r["sl_tp"]*POINT*100000:.0f} pip)  RR 1:1')
    print(f'  RSI BUY          : {r["rsi_buy_min"]} – {r["rsi_buy_max"]}')
    print(f'  RSI SELL         : {r["rsi_sell_min"]} – {r["rsi_sell_max"]}')
    print(f'  EMA buffer       : {r["ema_buf"]} pts')
    print(f'  Require EMA50    : {"YES – pullback phải chạm EMA50" if r["ema50_touch"] else "no – EMA21 is enough"}')
    print(f'  Min candle body  : {r["min_body"]} pts')
    print(f'  Session VN       : {r["sess_start"]}:00 – {r["sess_end"]}:00')
    print(f'  ─────────────────────────────────────────')
    print(f'  Total trades     : {r["n"]}')
    print(f'  Win Rate         : {r["wr"]:.1f}%  ← Target: >70%')
    print(f'  Net P&L          : ${r["pnl"]:.2f}')
    print(f'  Max Drawdown     : {r["dd"]:.1f}%')
    print(f'  Profit Factor    : {r["pf"]:.2f}')
    print(f'  Exits: TP={r["tp"]} SL={r["sl"]} BE={r["be"]} EOD={r["eod"]}')
    print()
    print(f'  → EA params to update in ScalpingEURUSD.mq5:')
    print(f'    InpSL         = {r["sl_tp"]}    // Stop Loss (pts)')
    print(f'    InpTP         = {r["sl_tp"]}    // Take Profit = SL for RR 1:1')
    print(f'    InpBEActivate = {int(r["sl_tp"]*0.5)} // BE at 50% SL')
    print(f'    InpBELock     = {max(3,int(r["sl_tp"]*0.1))}     // Lock profit after BE')
    print(f'    InpRSIBuyMin  = {r["rsi_buy_min"]}')
    print(f'    InpRSIBuyMax  = {r["rsi_buy_max"]}')
    print(f'    InpRSISellMin = {r["rsi_sell_min"]}')
    print(f'    InpRSISellMax = {r["rsi_sell_max"]}')
    print(f'    InpEMABuffer  = {r["ema_buf"]}')
    print(f'    InpSessStart  = {r["sess_start"]}   // VN hour')
    print(f'    InpSessEnd    = {r["sess_end"]}')


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print('='*60)
    print('  ScalpingEURUSD – Parameter Optimizer')
    print('  Goal: WR > 70%, RR 1:1 (SL = TP)')
    print('='*60)

    print('\nLoading eurusd_m5.csv ...')
    df_all = load_data('eurusd_m5.csv')
    df_all = compute_indicators(df_all)
    mask   = (df_all['datetime'] >= pd.Timestamp('2025-04-09')) & \
             (df_all['datetime'] <= pd.Timestamp('2026-04-09'))
    df     = df_all[mask].reset_index(drop=True)
    print(f'Test: {len(df):,} bars  {df["datetime"].iloc[0].date()} → {df["datetime"].iloc[-1].date()}')
    arr = build_arrays(df)

    # ── BASELINE ──────────────────────────────────────────────
    print('\n=== BASELINE: Original EA (SL=50 TP=100, RR 1:2) ===')
    bl_sess = make_sess(arr, 14, 22)
    bl_p = dict(sl_tp=50, rsi_buy_min=45, rsi_buy_max=70, rsi_sell_min=30, rsi_sell_max=55,
                ema_buf=30, ema50_touch=False, min_body=0, sess_start=14, sess_end=22)
    bl_p['tp_override'] = 100  # mark for special handling
    # Simulate baseline manually (TP=100 not SL=TP)
    bl_sigs = detect_signals(arr, bl_sess, bl_p)
    # Temporarily patch simulate for 1:2 RR
    bl_trades = _sim_baseline(arr, bl_sess, bl_sigs)
    bl_s = calc_stats(bl_trades)
    print(f'  N={bl_s["n"]}  WR={bl_s["wr"]:.1f}%  P&L=${bl_s["pnl"]:.2f}  DD={bl_s["dd"]:.1f}%')
    print(f'  TP={bl_s["tp"]} SL={bl_s["sl"]} EOD={bl_s["eod"]}')

    # ── PHASE 1: Key structural params ───────────────────────
    r1 = run_grid(arr, GRID_P1, P1_FIXED, 'PHASE 1 (structural: SL/TP + session + EMA50)',
                  min_wr=58.0, min_n=15)
    print_top(r1, n=15, label='PHASE 1 TOP 15')

    # Extract best structural params
    if not r1:
        print('\nPhase 1 found nothing. Lowering threshold...')
        r1 = run_grid(arr, GRID_P1, P1_FIXED, 'PHASE 1 retry', min_wr=50.0, min_n=10)
        if not r1:
            print('No configs found.'); return

    best1 = r1[0]
    print(f'\n  Best Phase-1: SL={best1["sl_tp"]} ema50={best1["ema50_touch"]} '
          f'body={best1["min_body"]} sess={best1["sess_start"]}-{best1["sess_end"]}VN '
          f'WR={best1["wr"]:.1f}%')

    # ── PHASE 2: Fine-tune RSI + buffer ──────────────────────
    fixed2 = {**P1_FIXED,
               'sl_tp':       best1['sl_tp'],
               'ema50_touch': best1['ema50_touch'],
               'min_body':    best1['min_body'],
               'sess_start':  best1['sess_start'],
               'sess_end':    best1['sess_end']}

    # Also test top-3 structural configs
    p2_fixed_variants = [fixed2]
    for r in r1[1:4]:
        v = {**P1_FIXED,
             'sl_tp': r['sl_tp'], 'ema50_touch': r['ema50_touch'],
             'min_body': r['min_body'], 'sess_start': r['sess_start'], 'sess_end': r['sess_end']}
        p2_fixed_variants.append(v)

    all_r2 = []
    for i, fv in enumerate(p2_fixed_variants):
        r2 = run_grid(arr, GRID_P2, fv,
                      f'PHASE 2 variant {i+1} (SL={fv["sl_tp"]} sess={fv["sess_start"]}-{fv["sess_end"]}VN ema50={fv["ema50_touch"]})',
                      min_wr=60.0, min_n=20)
        all_r2.extend(r2)

    all_r2.sort(key=lambda r: (-r['wr'], -r['pnl']))
    print_top(all_r2, n=20, label='PHASE 2 FINE-TUNE TOP 20')

    # Combined results
    all_results = all_r2 if all_r2 else r1
    if not all_results: all_results = r1
    all_results.sort(key=lambda r: (-r['wr'], -r['pnl']))

    if all_results:
        best = all_results[0]
        print_best(best)
        winhigh = [r for r in all_results if r['wr'] >= 70.0]
        if winhigh:
            print(f'\n>>> {len(winhigh)} configs achieve WR >= 70% <<<')
            print_best(winhigh[0])
        else:
            print(f'\nBest WR achieved: {best["wr"]:.1f}%')
            if best['wr'] >= 67:
                print('Close to 70%! Consider: partial-close at 0.5R, or EOD carry.')

        # Save
        out_rows = []
        for r in all_results:
            row = {k: v for k, v in r.items() if not callable(v)}
            out_rows.append(row)
        pd.DataFrame(out_rows).to_csv('optimize_results.csv', index=False)
        print(f'\nSaved {len(out_rows)} rows → optimize_results.csv')


def _sim_baseline(arr, sess, sigs, init_bal=100.0):
    """Baseline sim: SL=50pts TP=100pts RR 1:2."""
    hi=arr['hi']; lo=arr['lo']; cl=arr['cl']; op=arr['op']
    vn_h=arr['vn_h']; wd=arr['weekday']; dates=arr['dates']
    n=len(hi); sl=50*POINT; tp=100*POINT; sprd=6*POINT
    trades=[]; bal=init_bal; consec=0; day_date=None; day_loss=0.0; day_start=bal
    i=1
    while i < n-1:
        d=dates[i]
        if d!=day_date: day_date=d; day_loss=0.0; day_start=bal; consec=0
        if (day_start>0 and day_loss/day_start*100>=3) or consec>=2: i+=1; continue
        if sigs[i]==0: i+=1; continue
        sig=int(sigs[i])
        entry=op[i+1]+(sprd if sig==1 else 0.0)
        sl_p=entry-sl*sig; tp_p=entry+tp*sig
        lot=max(0.01, round(bal*1.0/100/(50*10)/0.01)*0.01); lot=min(lot,100.0)
        exit_px=0.0; reason=''; j=i+1
        while j<n:
            if sig==1:
                if lo[j]<=sl_p: exit_px=sl_p; reason='SL'; break
                if hi[j]>=tp_p: exit_px=tp_p; reason='TP'; break
            else:
                if hi[j]>=sl_p: exit_px=sl_p; reason='SL'; break
                if lo[j]<=tp_p: exit_px=tp_p; reason='TP'; break
            h=int(vn_h[j])
            if h>=21:
                nxt=sess[j+1] if j+1<n else False
                if not nxt: exit_px=cl[j]; reason='EOD'; break
            if wd[j]==4 and h>=21: exit_px=cl[j]; reason='EOD'; break
            j+=1
        if exit_px==0.0: i=j+1; continue
        pnl=round((exit_px-entry)*sig*lot*CONTRACT,2)
        trades.append({'pnl':pnl,'reason':reason,'dir':sig,'lots':lot,'entry':entry,'exit':exit_px})
        bal+=pnl
        if pnl<=0: day_loss+=abs(pnl); consec+=1
        else: consec=0
        i=j+1
    return trades


if __name__ == '__main__':
    main()
