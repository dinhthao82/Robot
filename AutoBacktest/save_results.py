#!/usr/bin/env python3
"""Save top results from optimizer run to CSV and print final summary."""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from optimize_v2 import (load_data, compute_indicators, build_arrays,
                          make_sess, detect_signals, simulate, calc_stats,
                          print_top, print_best, GRID_B_FOCUSED, BEST_STRUCTURAL)

POINT = 0.00001

def main():
    print('Reloading data and extracting top results...')
    df_all = load_data('eurusd_m5.csv')
    df_all = compute_indicators(df_all)
    mask   = (df_all['datetime'] >= pd.Timestamp('2025-04-09')) & \
             (df_all['datetime'] <= pd.Timestamp('2026-04-09'))
    df     = df_all[mask].reset_index(drop=True)
    arr    = build_arrays(df)

    # Re-simulate the confirmed top configs from optimizer output
    # (no grid search needed - just test these specific configs)
    TOP_CONFIGS = [
        # (sl, tp, rsi_bmin, rsi_bmax, rsi_smin, rsi_smax, buf, e50, body, ss, se)
        (25, 50, 60, 68, 38, 45, 15, True,  20, 16, 22),  # #1 PF=1.56
        (25, 50, 60, 65, 38, 45, 15, True,  20, 16, 22),  # #2 PF=1.55
        (25, 50, 60, 62, 38, 45, 15, True,  20, 16, 22),  # #3 PF=1.55
        (25, 50, 60, 68, 38, 48, 15, True,  20, 16, 22),  # #4 PF=1.52 N=193
        (25, 50, 60, 65, 38, 48, 15, True,  20, 16, 22),  # #5 PF=1.52 N=191
        (25, 50, 60, 62, 38, 48, 15, True,  20, 16, 22),  # #6 PF=1.51 N=182
        (25, 50, 62, 68, 38, 48, 15, True,  20, 16, 22),  # #7 PF=1.49 N=178
        (25, 50, 62, 65, 38, 48, 15, True,  20, 16, 22),  # #8 PF=1.48 N=176
    ]

    results = []
    for (sl, tp, rbmin, rbmax, rsmin, rsmax, buf, e50, body, ss, se) in TOP_CONFIGS:
        p = dict(sl_pts=sl, tp_pts=tp,
                 rsi_buy_min=rbmin, rsi_buy_max=rbmax,
                 rsi_sell_min=rsmin, rsi_sell_max=rsmax,
                 ema_buf=buf, ema50_touch=e50, min_body=body,
                 sess_start=ss, sess_end=se,
                 use_be=False, risk_pct=1.0, max_consec=2, max_dd=3.0)
        sess  = make_sess(arr, ss, se)
        sigs  = detect_signals(arr, sess, p)
        trades = simulate(arr, sess, sigs, p)
        s = calc_stats(trades)
        rr = tp / sl
        results.append({**p, **s, 'rr': rr})

    results.sort(key=lambda r: (-r['pf'], -r['wr']))

    print_top(results, label='TOP CONFIGS VERIFIED')
    print_best(results[0], 'OPTIMAL CONFIG (max PF)')

    # Config with most trades (more robust for live trading)
    best_by_n = max(results, key=lambda r: r['n'] if r['pf'] >= 1.2 else 0)
    if best_by_n['n'] != results[0]['n']:
        print_best(best_by_n, 'BEST CONFIG (most trades, PF>=1.2)')

    # Save CSV
    out_rows = [{k: v for k,v in r.items() if not callable(v)} for r in results]
    pd.DataFrame(out_rows).to_csv('optimize_v2_results.csv', index=False)
    print(f'\nSaved {len(out_rows)} rows to optimize_v2_results.csv')

if __name__ == '__main__':
    main()
