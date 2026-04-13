#!/usr/bin/env python3
"""
optimize_v2.py – Profitability-Focused Optimization

Problem: Phase 2 found WR=76.8% but P&L=-$9.36 (PF=0.42)
Cause:
  - 76% BE exits earn ~$0.03 each (BE lock=3pts × tiny lot)
  - 23% SL hits lose $0.25 each (25pts × 0.01 lot)
  - Real TP hits = only 2/280 = 0.7%

Key insight:
  BE activates at 12.5pts (50% of SL=25). 76% of trades reach
  12.5pts then RETRACE back to entry/SL.
  → If TP = 12pts (no BE), those 76% become REAL TP wins at +12pts
  → WR ≈ 76%, PF = 0.76*12 / (0.24*25) ≈ 1.52  → PROFITABLE!

This optimizer:
  1. TP sweep: fix best entry, vary TP 8–50pts, no BE
  2. Phase A: vary SL/TP + structural params, find best PF
  3. Phase B: fine-tune RSI + EMA buffer around top-4 configs
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
        e21=df['e21'].values, e50=df['e50'].values,
        e21p=df['e21p'].values, e50p=df['e50p'].values,
        cp=df['cp'].values, rsi=df['rsi'].values,
        hi=df['high'].values, lo=df['low'].values,
        cl=df['close'].values, op=df['open'].values,
        body=df['body'].values,
        vn_h=vn_dt.dt.hour.values.astype(np.int8),
        weekday=vn_dt.dt.weekday.values.astype(np.int8),
        dates=vn_dt.dt.date.values,
    )

def make_sess(arr, s_start, s_end):
    return (arr['vn_h'] >= s_start) & (arr['vn_h'] < s_end)

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
    sig[buy] = 1; sig[sell] = -1
    return sig

# ─────────────────────────────────────────────────────────────
def simulate(arr, sess, sigs, p, init_bal=100.0):
    """
    use_be=False: pure TP/SL, no break-even
    use_be=True:  BE activates at be_frac*SL, locks be_lock pts
    p['sl_pts'] / p['tp_pts'] are the SL/TP in points
    """
    hi=arr['hi']; lo=arr['lo']; cl=arr['cl']; op=arr['op']
    vn_h=arr['vn_h']; wd=arr['weekday']; dates=arr['dates']
    n = len(hi)

    sl_p   = p['sl_pts'] * POINT
    tp_p   = p['tp_pts'] * POINT
    sprd   = 6 * POINT
    s_end  = p['sess_end']
    risk   = p.get('risk_pct', 1.0)
    max_cl = p.get('max_consec', 2)
    max_dd = p.get('max_dd', 3.0)
    use_be = p.get('use_be', False)
    be_a   = p['sl_pts'] * p.get('be_frac', 0.5) * POINT if use_be else 0.0
    be_l   = max(3, p['sl_pts'] * 0.1) * POINT           if use_be else 0.0

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

        sig   = int(sigs[i])
        entry = op[i+1] + (sprd if sig==1 else 0.0)
        sl_price = entry - sl_p*sig
        tp_price = entry + tp_p*sig
        lot   = max(0.01, round(bal*risk/100/(p['sl_pts']*10)/0.01)*0.01)
        lot   = min(lot, 100.0)

        cur_sl = sl_price; be_done = False
        exit_px = 0.0; reason = ''; j = i+1

        while j < n:
            if use_be and not be_done:
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
    """Returns stats with non-conflicting key names for exit counts:
       n_tp, n_sl, n_be, n_eod  (not 'tp'/'sl' which are pts in params)
    """
    if not trades:
        return dict(n=0, wins=0, wr=0, pnl=0, dd=0, pf=0,
                    n_tp=0, n_sl=0, n_be=0, n_eod=0)
    n=len(trades)
    wins=sum(1 for t in trades if t['pnl']>0)
    pnl=sum(t['pnl'] for t in trades)
    gw=sum(t['pnl'] for t in trades if t['pnl']>0)
    gl=abs(sum(t['pnl'] for t in trades if t['pnl']<=0))
    pf = gw/gl if gl>0 else 99.0
    bal=init_bal; peak=bal; mdd=0.0
    for t in trades:
        bal+=t['pnl']; peak=max(peak,bal)
        mdd=max(mdd,(peak-bal)/peak*100 if peak>0 else 0)
    rs={}
    for t in trades: rs[t['reason']]=rs.get(t['reason'],0)+1
    return dict(n=n, wins=wins, wr=wins/n*100, pnl=pnl, dd=mdd, pf=pf,
                n_tp=rs.get('TP',0), n_sl=rs.get('SL',0),
                n_be=rs.get('BE',0), n_eod=rs.get('EOD',0))


# ─────────────────────────────────────────────────────────────
# STEP 1: TP SWEEP with best entry conditions from Phase 2
# ─────────────────────────────────────────────────────────────
BEST_ENTRY = dict(
    rsi_buy_min=60, rsi_buy_max=60,
    rsi_sell_min=33, rsi_sell_max=48,
    ema_buf=25, ema50_touch=True,
    min_body=20,
    sess_start=16, sess_end=22,
)

def tp_sweep(arr):
    """Fix SL=25pts, vary TP from 8 to 50 pts, no BE."""
    print('\n' + '='*80)
    print('  STEP 1: TP SWEEP (SL=25 fixed, TP=8..50 pts, NO Break-Even)')
    print('  Entry: RSI-BUY=60-60, RSI-SELL=33-48, EMA50-touch, body>=20, sess=16-22VN')
    print('='*80)
    print(f"  {'TP':>4}  {'RR':>6}  {'N':>4}  {'TP_n':>5}  {'SL_n':>5}  {'WR%':>6}  {'P&L':>8}  {'PF':>5}  {'DD%':>5}")
    print('  ' + '-'*65)

    p_base = {**BEST_ENTRY,
              'sl_pts': 25, 'use_be': False,
              'risk_pct': 1.0, 'max_consec': 2, 'max_dd': 3.0}
    sess = make_sess(arr, p_base['sess_start'], p_base['sess_end'])
    sigs = detect_signals(arr, sess, p_base)
    n_signals = int((sigs != 0).sum())
    print(f'  Total signals: {n_signals}')
    print()

    best_pf_row = None
    for tp in range(8, 52, 2):
        p = {**p_base, 'tp_pts': tp}
        trades = simulate(arr, sess, sigs, p)
        s = calc_stats(trades)
        if s['n'] < 5: continue
        rr = tp / 25.0
        marker = ''
        if s['pf'] >= 1.0: marker += ' <--'
        if s['pf'] >= 1.2 and s['wr'] >= 60.0: marker += ' ***'
        print(f"  {tp:>3}p  1:{rr:>4.2f}  {s['n']:>4}  {s['n_tp']:>5}  {s['n_sl']:>5}  "
              f"{s['wr']:>5.1f}%  ${s['pnl']:>7.2f}  {s['pf']:>5.2f}  {s['dd']:>5.1f}%{marker}")
        if best_pf_row is None or s['pf'] > best_pf_row['pf']:
            best_pf_row = {'tp_pts': tp, 'rr': rr, **s}

    print()
    if best_pf_row:
        print(f'  >>> Best TP: {best_pf_row["tp_pts"]}pts  RR=1:{best_pf_row["rr"]:.2f}  '
              f'WR={best_pf_row["wr"]:.1f}%  P&L=${best_pf_row["pnl"]:.2f}  PF={best_pf_row["pf"]:.2f}')
    return best_pf_row


# ─────────────────────────────────────────────────────────────
# STEP 2: FULL GRID – vary (SL_pts, TP_pts) + entry conditions
# ─────────────────────────────────────────────────────────────
# Phase A: Vary SL_pts, TP_pts, session only (fix RSI to Phase-2 best)
GRID_A = {
    'sl_pts':      [15, 20, 25, 30],
    'tp_pts':      [8, 10, 12, 15, 18, 20, 25],
    'ema50_touch': [True, False],
    'min_body':    [0, 10, 15, 20],
    'sess_start':  [14, 15, 16],
    'sess_end':    [19, 20, 21, 22],
}
# 4*7*2*4*3*4 = 2688 combos
FIXED_A = dict(rsi_buy_min=58, rsi_buy_max=65, rsi_sell_min=30, rsi_sell_max=50,
               ema_buf=25, use_be=False, risk_pct=1.0, max_consec=2, max_dd=3.0)

# Phase B: Fix best SL/TP, vary RSI + buffer
GRID_B = {
    'rsi_buy_min':  [52, 55, 57, 58, 60, 62, 65],
    'rsi_buy_max':  [58, 60, 62, 65, 68, 70],
    'rsi_sell_min': [28, 30, 33, 35, 38],
    'rsi_sell_max': [45, 48, 50, 52, 55],
    'ema_buf':      [10, 15, 20, 25, 30, 35],
    'min_body':     [0, 5, 10, 15, 20],
}
# 7*6*5*5*6*5 = 31,500 combos per structural config


def run_grid(arr, grid_params, fixed_params, label, min_pf=1.0, min_wr=50.0, min_n=15):
    keys = list(grid_params.keys())
    vals = [grid_params[k] for k in keys]
    total = 1
    for v in vals: total *= len(v)

    print(f'\n{label}: {total:,} combinations...')
    results = []; t0 = time.time()
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

        if s['n'] >= min_n and s['pf'] >= min_pf and s['wr'] >= min_wr:
            rr = p['tp_pts'] / p['sl_pts']
            results.append({**p, **s, 'rr': rr})

        if (idx+1) % 300 == 0:
            el  = time.time()-t0
            eta = (total-idx-1)/((idx+1)/el) if el>0 else 0
            best_pf = max((r['pf'] for r in results), default=0)
            best_wr = max((r['wr'] for r in results), default=0)
            print(f'  [{idx+1:5d}/{total}]  {len(results):3d} pass  ETA {eta:.0f}s  '
                  f'bestPF={best_pf:.2f}  bestWR={best_wr:.1f}%')

    t = time.time()-t0
    print(f'  Done {t:.1f}s  =>  {len(results)} configs PF>={min_pf} WR>={min_wr}%')
    results.sort(key=lambda r: (-r['pf'], -r['wr']))
    return results


def print_top(results, n=20, label=''):
    if not results:
        print('  No results.'); return
    if label:
        print(f'\n{"="*105}\n  {label}\n{"="*105}')
    hdr = (f"  {'#':>3}  {'SL':>4}  {'TP':>4}  {'RR':>5}  "
           f"{'RSIbuy':>8}  {'RSIsell':>8}  {'buf':>3}  {'E50?':>4}  {'Body':>4}  {'Sess':>7}  "
           f"{'N':>4}  {'WR%':>6}  {'TP_n':>5}  {'SL_n':>5}  {'P&L':>8}  {'DD%':>5}  {'PF':>5}")
    print(hdr)
    print('  ' + '-'*102)
    for i, r in enumerate(results[:n], 1):
        rb = f"{r['rsi_buy_min']}-{r['rsi_buy_max']}"
        rs = f"{r['rsi_sell_min']}-{r['rsi_sell_max']}"
        se = f"{r['sess_start']}-{r['sess_end']}VN"
        e5 = 'Y' if r['ema50_touch'] else 'n'
        rr = r.get('rr', r['tp_pts']/r['sl_pts'])
        print(f"  {i:>3}  {r['sl_pts']:>3}p  {r['tp_pts']:>3}p  1:{rr:>3.1f}  "
              f"{rb:>8}  {rs:>8}  {r['ema_buf']:>3}  {e5:>4}  {r['min_body']:>3}p  {se:>7}  "
              f"{r['n']:>4}  {r['wr']:>5.1f}%  {r['n_tp']:>5}  {r['n_sl']:>5}  "
              f"${r['pnl']:>7.2f}  {r['dd']:>5.1f}%  {r['pf']:>5.2f}")


def print_best(r, label='OPTIMAL CONFIG'):
    sl = r['sl_pts']; tp = r['tp_pts']
    rr = r.get('rr', tp/sl)
    print(f'\n{"="*65}')
    print(f'  {label}  WR={r["wr"]:.1f}%  PF={r["pf"]:.2f}  P&L=${r["pnl"]:.2f}')
    print(f'{"="*65}')
    print(f'  SL               : {sl} pts  ({sl/10:.1f} pip)')
    print(f'  TP               : {tp} pts  ({tp/10:.1f} pip)  RR 1:{rr:.2f}')
    print(f'  RSI BUY          : {r["rsi_buy_min"]} – {r["rsi_buy_max"]}')
    print(f'  RSI SELL         : {r["rsi_sell_min"]} – {r["rsi_sell_max"]}')
    print(f'  EMA buffer       : {r["ema_buf"]} pts')
    print(f'  Require EMA50    : {"YES (pullback to EMA50)" if r["ema50_touch"] else "no – EMA21 is enough"}')
    print(f'  Min candle body  : {r["min_body"]} pts')
    print(f'  Session VN       : {r["sess_start"]}:00 – {r["sess_end"]}:00')
    print(f'  ─────────────────────────────────')
    print(f'  Total trades     : {r["n"]}')
    print(f'  Win Rate         : {r["wr"]:.1f}%')
    print(f'  Net P&L ($100)   : ${r["pnl"]:.2f}')
    print(f'  Max Drawdown     : {r["dd"]:.1f}%')
    print(f'  Profit Factor    : {r["pf"]:.2f}')
    print(f'  Exits: TP={r["n_tp"]}  SL={r["n_sl"]}  BE={r["n_be"]}  EOD={r["n_eod"]}')
    print()
    print(f'  → EA params to update in ScalpingEURUSD.mq5:')
    print(f'    InpSL                = {sl}')
    print(f'    InpTP                = {tp}')
    print(f'    InpBEActivate        = 0   // Disable BE (pure TP/SL)')
    print(f'    InpBELock            = 0')
    print(f'    InpRSIBuyMin         = {r["rsi_buy_min"]}')
    print(f'    InpRSIBuyMax         = {r["rsi_buy_max"]}')
    print(f'    InpRSISellMin        = {r["rsi_sell_min"]}')
    print(f'    InpRSISellMax        = {r["rsi_sell_max"]}')
    print(f'    InpEMABuffer         = {r["ema_buf"]}')
    print(f'    InpSessStart         = {r["sess_start"]}')
    print(f'    InpSessEnd           = {r["sess_end"]}')
    print(f'    InpRequireEMA50Touch = {"true" if r["ema50_touch"] else "false"}')
    print(f'    InpMinBodyPts        = {r["min_body"]}')


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print('='*65)
    print('  ScalpingEURUSD v2 – Profitability-Focused Optimizer')
    print('  Goal: PF > 1.2 AND WR > 55% (profitable + consistent)')
    print('='*65)

    print('\nLoading eurusd_m5.csv ...')
    df_all = load_data('eurusd_m5.csv')
    df_all = compute_indicators(df_all)
    mask   = (df_all['datetime'] >= pd.Timestamp('2025-04-09')) & \
             (df_all['datetime'] <= pd.Timestamp('2026-04-09'))
    df     = df_all[mask].reset_index(drop=True)
    print(f'Period: {len(df):,} bars  {df["datetime"].iloc[0].date()} to {df["datetime"].iloc[-1].date()}')
    arr = build_arrays(df)

    # ── STEP 1: TP SWEEP ──────────────────────────────────────
    best_sweep = tp_sweep(arr)

    # ── PHASE A: Grid over SL/TP + structural params ──────────
    r_a = run_grid(arr, GRID_A, FIXED_A,
                   'PHASE A – SL/TP + session + EMA50 (RSI fixed at broad range)',
                   min_pf=1.0, min_wr=45.0, min_n=15)

    if not r_a:
        print('\nPhase A found nothing. Lowering thresholds...')
        r_a = run_grid(arr, GRID_A, FIXED_A,
                       'PHASE A retry (min_pf=0.6)',
                       min_pf=0.6, min_wr=40.0, min_n=10)

    print_top(r_a[:20], label='PHASE A TOP 20 (sorted by PF)')

    # ── PHASE B: Fine-tune RSI + buffer (top-4 structural) ────
    all_b = []
    if r_a:
        for i, r in enumerate(r_a[:4]):
            fixed_b = {**FIXED_A,
                       'sl_pts': r['sl_pts'], 'tp_pts': r['tp_pts'],
                       'ema50_touch': r['ema50_touch'],
                       'min_body': r['min_body'],
                       'sess_start': r['sess_start'],
                       'sess_end': r['sess_end']}
            rb = run_grid(arr, GRID_B, fixed_b,
                          f'PHASE B #{i+1}  SL={r["sl_pts"]} TP={r["tp_pts"]} '
                          f'sess={r["sess_start"]}-{r["sess_end"]}VN ema50={r["ema50_touch"]}',
                          min_pf=1.0, min_wr=45.0, min_n=15)
            all_b.extend(rb)
        all_b.sort(key=lambda r: (-r['pf'], -r['wr']))
        print_top(all_b[:20], label='PHASE B FINE-TUNE TOP 20 (sorted by PF)')

    # ── FINAL SUMMARY ─────────────────────────────────────────
    all_results = all_b if all_b else r_a
    if not all_results:
        print('\nNo profitable configs found.')
        return

    all_results.sort(key=lambda r: (-r['pf'], -r['wr']))
    best_pf  = all_results[0]
    print_best(best_pf, 'BEST CONFIG by Profit Factor')

    wrgood = [r for r in all_results if r['wr'] >= 60.0 and r['pf'] >= 1.0]
    if wrgood:
        wrgood.sort(key=lambda r: (-r['wr'], -r['pf']))
        print_best(wrgood[0], 'BEST CONFIG WR>=60% + PF>=1.0')

    wr70pf = [r for r in all_results if r['wr'] >= 70.0 and r['pf'] >= 1.0]
    if wr70pf:
        wr70pf.sort(key=lambda r: (-r['pf'], -r['wr']))
        print_best(wr70pf[0], 'BEST CONFIG WR>=70% + PF>=1.0 (ideal)')
    else:
        best_wr = max(all_results, key=lambda r: r['wr'])
        print(f'\n  Note: No config achieves WR>=70% + PF>=1.0.')
        print(f'  Best WR with PF>=1.0: WR={max((r["wr"] for r in all_results if r["pf"]>=1.0), default=0):.1f}%')
        print(f'  Best WR overall: {best_wr["wr"]:.1f}% (PF={best_wr["pf"]:.2f})')

    # Save
    out_rows = [{k: v for k,v in r.items() if not callable(v)} for r in all_results[:300]]
    pd.DataFrame(out_rows).to_csv('optimize_v2_results.csv', index=False)
    print(f'\nSaved {len(out_rows)} rows → optimize_v2_results.csv')
    print('\nDone.')


if __name__ == '__main__':
    main()
