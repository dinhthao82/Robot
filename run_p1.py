"""
Phase 1 Optimizer — Exness Pro | EUR/USD | $100 | Day Trade
Strategy: EMA Trend Follower (thay thế GridHedge thất bại)
Chạy : python run_p1.py
Output: output_p1.csv
"""

import sys
import time
import numpy as np
import pandas as pd

# Fix Windows console UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import fetch_data, run_backtest, optimize, daily_stats, SYMBOL_CONFIG

# ══════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════
SYMBOL       = "EURUSD"
START_DATE   = (pd.Timestamp.today() - pd.Timedelta(days=729)).strftime("%Y-%m-%d")
END_DATE     = pd.Timestamp.today().strftime("%Y-%m-%d")
BALANCE_INIT = 100.0
SPREAD_PIPS  = 0.7      # Exness Pro avg
ATR_PERIOD   = 14
MAX_TRADES   = 2        # max entries/day (incl. re-entry after SL)
SL_COOLDOWN  = 3        # bars to wait after SL before re-entering
ATR_PERIOD   = 14

# EMA periods
EMA_FAST_LIST = [5, 8, 13]
EMA_SLOW_LIST = [21, 34, 55]

# ATR multipliers
ATR_TP_LIST = [1.5, 2.0, 2.5, 3.0]   # TP distance
ATR_SL_LIST = [0.7, 1.0, 1.5]         # SL distance

# EMA slope filter (relative to price): 0 = off, 2e-5 = moderate, 5e-5 = strict
# Skips entries when EMA is flat (choppy/ranging market)
MIN_SLOPE_LIST = [0.0, 2e-5, 5e-5]

# Session window (GMT+7) — London + NY
TBEGIN_LIST = [14, 15, 16, 17]
TEND_LIST   = [20, 21, 22]

OUTPUT_FILE = "output_p1.csv"
# ══════════════════════════════════════════════════════════════════


def count_combos():
    from itertools import product
    return sum(
        1 for ef, es, atp, asl, tb, te, ms
        in product(EMA_FAST_LIST, EMA_SLOW_LIST, ATR_TP_LIST, ATR_SL_LIST,
                   TBEGIN_LIST, TEND_LIST, MIN_SLOPE_LIST)
        if ef < es and tb < te
    )


def main():
    print("=" * 62)
    print("  PHASE 1 OPTIMIZER — EMA Trend Follower")
    print(f"  {SYMBOL} | ${BALANCE_INIT} | Exness Pro | Day Trade")
    print(f"  Spread: {SPREAD_PIPS} pip | Max {MAX_TRADES} trades/day")
    print("=" * 62)

    # ── Step 1: Fetch data ─────────────────────────────────────
    print(f"\n[1/4] Tải dữ liệu {SYMBOL} ({START_DATE} → {END_DATE})...")
    t0 = time.time()
    try:
        df, cfg = fetch_data(SYMBOL, START_DATE, END_DATE)
    except Exception as e:
        print(f"❌ Lỗi: {e}")
        return

    n_days = (df.index[-1] - df.index[0]).days
    print(f"    ✓ {len(df):,} bars | {df.index[0].date()} → {df.index[-1].date()} ({n_days} ngày)")

    # ── Step 2: Count combos ───────────────────────────────────
    total_combos = count_combos()
    print(f"\n[2/4] Search space:")
    print(f"    EMA fast   : {EMA_FAST_LIST}")
    print(f"    EMA slow   : {EMA_SLOW_LIST}")
    print(f"    ATR_TP     : {ATR_TP_LIST}")
    print(f"    ATR_SL     : {ATR_SL_LIST}")
    print(f"    Min slope  : {MIN_SLOPE_LIST}")
    print(f"    TBegin     : {TBEGIN_LIST}")
    print(f"    TEnd       : {TEND_LIST}")
    print(f"    SL cooldown: {SL_COOLDOWN} bars")
    print(f"    → Total valid combos: {total_combos:,}")

    # ── Step 3: Optimize ──────────────────────────────────────
    print(f"\n[3/4] Grid search...")
    _last_pct = [-1]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("Rank,EMA_fast,EMA_slow,ATR_TP,ATR_SL,TBegin,TEnd,Min_slope,"
                "N_trades,Total_profit,Daily_ret_pct,Win_rate,Sharpe,Max_DD_pct,Score\n")

        def on_progress(done, total):
            pct = int(done / total * 100)
            if pct % 5 == 0 and pct != _last_pct[0]:
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done) if done > 0 else 0
                print(f"    {pct:3d}% [{done:,}/{total:,}]  "
                      f"elapsed={elapsed:.0f}s  ETA={eta:.0f}s")
                _last_pct[0] = pct

        def on_result(rank, r):
            f.write(
                f"{rank},{r['EMA_fast']},{r['EMA_slow']},{r['ATR_TP']},{r['ATR_SL']},"
                f"{int(r['TBegin'])},{int(r['TEnd'])},{r['Min_slope']:.2e},{r['N_trades']},"
                f"{r['Total_profit']},{r['Daily_ret_pct']},{r['Win_rate']:.4f},"
                f"{r['Sharpe']:.4f},{r['Max_DD_pct']},{r['Score']:.4f}\n"
            )
            f.flush()

        results_df = optimize(
            df,
            pip_size        = cfg["pip"],
            pip_usd_std     = cfg["pip_usd"],
            ema_fast_list   = EMA_FAST_LIST,
            ema_slow_list   = EMA_SLOW_LIST,
            atr_tp_list     = ATR_TP_LIST,
            atr_sl_list     = ATR_SL_LIST,
            tbegin_list     = TBEGIN_LIST,
            tend_list       = TEND_LIST,
            min_slope_list  = MIN_SLOPE_LIST,
            balance_init    = BALANCE_INIT,
            max_trades_day  = MAX_TRADES,
            sl_cooldown     = SL_COOLDOWN,
            atr_period      = ATR_PERIOD,
            spread_pips     = SPREAD_PIPS,
            progress_cb     = on_progress,
            result_cb       = on_result,
        )

    elapsed_total = time.time() - t0
    print(f"    ✓ Xong {elapsed_total:.1f}s — {len(results_df)} kết quả hợp lệ")

    if results_df.empty:
        print("\n❌ Không có kết quả. Kiểm tra dữ liệu.")
        return

    # ── Step 4: Show results ───────────────────────────────────
    print(f"\n[4/4] Kết quả → {OUTPUT_FILE}")

    good = results_df[
        (results_df["Daily_ret_pct"] >= 1.0) &
        (results_df["Max_DD_pct"]    <= 20.0) &
        (results_df["Win_rate"]      >= 0.50)
    ]
    print(f"\n{'─'*62}")
    print(f"  Đạt tiêu chí (daily ≥ 1%, DD ≤ 20%, WR ≥ 50%): {len(good)} combo")
    print(f"{'─'*62}")

    top = results_df.head(10)
    print(f"\n  TOP 10 (by Score):")
    print(f"  {'Rank':>4} {'EF':>3} {'ES':>3} {'TP':>4} {'SL':>4} "
          f"{'TB':>3} {'TE':>3} {'WR%':>6} {'Daily%':>7} {'DD%':>6} {'Score':>8}")
    print(f"  {'─'*62}")
    for i, r in top.iterrows():
        flag = " ✓" if r["Daily_ret_pct"] >= 1.0 and r["Max_DD_pct"] <= 20.0 else ""
        print(f"  {i+1:>4} {int(r['EMA_fast']):>3} {int(r['EMA_slow']):>3} "
              f"{r['ATR_TP']:>4.1f} {r['ATR_SL']:>4.1f} "
              f"{int(r['TBegin']):>3} {int(r['TEnd']):>3} "
              f"{r['Win_rate']*100:>5.1f}% "
              f"{r['Daily_ret_pct']:>6.3f}% "
              f"{r['Max_DD_pct']:>5.1f}% "
              f"{r['Score']:>8.2f}{flag}")

    best = results_df.iloc[0]
    print(f"\n{'═'*62}")
    print(f"  BEST PARAMS:")
    print(f"  EMA fast  = {int(best['EMA_fast'])} | EMA slow = {int(best['EMA_slow'])}")
    print(f"  ATR TP    = {best['ATR_TP']}x ATR | ATR SL = {best['ATR_SL']}x ATR")
    print(f"  Min slope = {best['Min_slope']:.2e}")
    print(f"  TBegin    = {int(best['TBegin']):02d}:00 GMT+7 | TEnd = {int(best['TEnd']):02d}:00")
    print(f"  Win rate  = {best['Win_rate']*100:.1f}%")
    print(f"  Daily     = {best['Daily_ret_pct']:.3f}%/day")
    print(f"  Max DD    = {best['Max_DD_pct']:.2f}%")
    print(f"{'═'*62}")

    # Detailed backtest on best params
    print(f"\n  Backtest chi tiết với best params...")
    cyc, eq = run_backtest(
        df,
        ema_fast       = int(best["EMA_fast"]),
        ema_slow       = int(best["EMA_slow"]),
        atr_tp         = float(best["ATR_TP"]),
        atr_sl         = float(best["ATR_SL"]),
        tbegin         = int(best["TBegin"]),
        tend           = int(best["TEnd"]),
        balance_init   = BALANCE_INIT,
        sl_cooldown    = SL_COOLDOWN,
        min_slope      = float(best["Min_slope"]),
        pip_size       = cfg["pip"],
        pip_usd_std    = cfg["pip_usd"],
        max_trades_day = MAX_TRADES,
        spread_pips    = SPREAD_PIPS,
    )

    if not cyc.empty:
        d = daily_stats(cyc, BALANCE_INIT)
        if not d.empty:
            pos_days = (d["daily_pct"] >= 1.0).sum()
            neg_days = (d["daily_pct"] < 0).sum()
            avg_day  = d["daily_pct"].mean()
            print(f"\n  Daily stats ({len(d)} ngày trade):")
            print(f"    Ngày ≥ 1%  : {pos_days} ({pos_days/len(d)*100:.0f}%)")
            print(f"    Ngày thua  : {neg_days} ({neg_days/len(d)*100:.0f}%)")
            print(f"    Avg daily  : {avg_day:.3f}%")
            print(f"    Balance cuối: ${cyc['balance_end'].iloc[-1]:.2f}")
            print(f"    Total return: {(cyc['balance_end'].iloc[-1]/BALANCE_INIT - 1)*100:.1f}%")

        # Reason breakdown
        reason = cyc.groupby("reason").agg(
            count=("profit", "count"),
            total=("profit", "sum"),
            avg=("profit", "mean"),
        ).round(3)
        print(f"\n  By reason:\n{reason.to_string()}")

        cyc.to_csv("cycles_best_p1.csv", index=False)
        print(f"\n  Chi tiết → cycles_best_p1.csv")

    print(f"\n  Tiếp: python -m streamlit run app.py")
    print()


if __name__ == "__main__":
    main()
