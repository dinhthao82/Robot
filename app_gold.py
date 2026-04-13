"""
app_gold.py — Streamlit Optimizer UI for NewBotGold (XAUUSD Grid Strategy)
Run: streamlit run app_gold.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd
import numpy as np

from engine_gold import load_data, run_backtest, optimize, INIT_BAL

st.set_page_config(page_title="NewBotGold Optimizer | XAUUSD", layout="wide")
st.title("NewBotGold — BUY-Only Martingale Grid | XAUUSD")
st.caption("Based on NewBotGold_V1.mq5 (AutoProject V2 v1.11 — P1 Top-1 params)")

st.info(
    "**Calibration note:** Spread confirmed from real Exness GOLD tester log — "
    "avg 0.309 USD, median **0.300 USD** per 0.01 lot (1,167 trade samples). "
    "Engine uses bar-open for first entry (matches MQL5 'first tick' behavior). "
    "Grid adds checked against bar low at each grid level."
)

# ─── Sidebar ───────────────────────────────────────────────────────────────
st.sidebar.header("Grid Parameters")

grid_step_sel = st.sidebar.multiselect(
    "Grid Step (USD drop → add BUY)",
    [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
    default=[1.0, 2.0, 3.0],
)
max_pos_sel = st.sidebar.multiselect(
    "Max Positions per cycle",
    [5, 8, 10, 15, 20],
    default=[10, 15],
)
tp_pct_sel = st.sidebar.multiselect(
    "TP % of balance (close all)",
    [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0],
    default=[20.0, 30.0],
)
max_dd_sel = st.sidebar.multiselect(
    "Max Daily DD % (stop today)",
    [10.0, 15.0, 20.0, 25.0, 30.0],
    default=[20.0],
)

st.sidebar.divider()
st.sidebar.header("Session Window (GMT+7)")
sh_sel = st.sidebar.multiselect("Start Hour", [7, 8, 9, 10], default=[8])
eh_sel = st.sidebar.multiselect("End Hour",   [20, 21, 22, 23], default=[23])

st.sidebar.divider()
st.sidebar.header("V2 Day Trading")
force_close_min  = st.sidebar.number_input("Force Close (min before end)", 0, 60, 5, 1,
    help="MT5 V2: close all positions X minutes before session end (ForceCloseMin=5)")
no_new_order_min = st.sidebar.number_input("No New Orders (min before end)", 0, 60, 10, 1,
    help="MT5 V2: stop opening/adding positions X minutes before session end (NoNewOrderMin=10)")

st.sidebar.divider()
st.sidebar.header("Risk / Money")
stop_bot_pct  = st.sidebar.number_input("Permanent Halt below (% start)", 10.0, 90.0, 50.0, 5.0)
balance_per   = st.sidebar.number_input("Balance per 0.01 lot (USD)", 100.0, 5000.0, 1000.0, 100.0)
spread_usd    = st.sidebar.number_input(
    "Spread cost per 0.01 lot (USD)", 0.01, 5.0, 0.30, 0.05,
    help="Calibrated from real Exness GOLD tester log: avg=0.309, median=0.300 USD (1,167 samples)")
init_balance  = st.sidebar.number_input("Initial Balance (USD)", 50.0, 10000.0, 100.0, 50.0)

st.sidebar.divider()
st.sidebar.header("Data Source")
data_interval = st.sidebar.radio(
    "Bar Interval",
    ["1h (2 years, fast)", "15m (60 days, accurate)"],
    index=0,
    help="15m is closer to MT5 M15 tester. 1h has more history for optimization."
)
interval_str = "1h" if "1h" in data_interval else "15m"
data_period  = "60d" if interval_str == "15m" else st.sidebar.selectbox(
    "Data Period", ["1y", "2y"], index=1
)

# ─── Combo count ────────────────────────────────────────────────────────────
valid_pairs = [(sh, eh) for sh in sh_sel for eh in eh_sel if sh < eh]
n_combos = (
    len(grid_step_sel) * len(max_pos_sel) * len(tp_pct_sel)
    * len(max_dd_sel) * len(valid_pairs)
)
st.sidebar.metric("Total Combinations", n_combos)

# ─── Run button ─────────────────────────────────────────────────────────────
run_btn = st.sidebar.button("Run Optimizer", type="primary",
                             disabled=(n_combos == 0))

if run_btn:
    # Load data
    with st.spinner(f"Downloading GOLD (GC=F) data [{interval_str}]..."):
        try:
            df = load_data(period=data_period, interval=interval_str)
        except Exception as e:
            st.error(f"Data download failed: {e}")
            st.stop()

    st.info(
        f"Data: **{df.index[0].date()}** to **{df.index[-1].date()}** "
        f"| **{len(df):,}** bars | "
        f"Price range: ${df['Low'].min():.0f} – ${df['High'].max():.0f}"
    )

    # Progress bar
    prog   = st.progress(0.0, text="Running backtests...")
    status = st.empty()

    def update_progress(done, total):
        pct = done / total
        prog.progress(pct, text=f"Running backtests... {done}/{total}")

    with st.spinner(f"Optimizing {n_combos} combos..."):
        results = optimize(
            df,
            grid_step_list     = grid_step_sel,
            max_positions_list = max_pos_sel,
            tp_pct_list        = tp_pct_sel,
            max_dd_pct_list    = max_dd_sel,
            start_hour_list    = sh_sel,
            end_hour_list      = eh_sel,
            stop_bot_pct       = stop_bot_pct,
            balance_per        = balance_per,
            min_lot            = 0.01,
            max_lot            = 2.00,
            spread_usd         = spread_usd,
            init_balance       = init_balance,
            force_close_min    = int(force_close_min),
            no_new_order_min   = int(no_new_order_min),
            max_workers        = 1,
            progress_cb        = update_progress,
        )

    prog.empty()

    if results.empty:
        st.error("No results produced. Check parameter selections.")
        st.stop()

    # ─── Top Results Table ──────────────────────────────────────────────
    st.subheader("Top 20 Combinations")

    display_cols = [
        "cagr_daily_pct", "daily_ret_pct", "final_balance", "net_gain_pct",
        "win_rate", "n_cycles", "avg_n_pos", "max_depth",
        "actual_max_dd_pct", "halted",
        "grid_step", "max_positions", "tp_pct", "max_dd_pct",
        "start_hour_gmt7", "end_hour_gmt7",
    ]
    show_cols = [c for c in display_cols if c in results.columns]

    top20 = results.head(20)
    st.dataframe(
        top20[show_cols].style.format({
            "cagr_daily_pct":    "{:.4f}%",
            "daily_ret_pct":     "{:.4f}%",
            "final_balance":     "${:.2f}",
            "net_gain_pct":      "{:.1f}%",
            "win_rate":          "{:.1f}%",
            "avg_n_pos":         "{:.1f}",
            "actual_max_dd_pct": "{:.1f}%",
        }),
        use_container_width=True,
    )

    # ─── Best Config Detail ─────────────────────────────────────────────
    st.subheader("Best Configuration — Detailed Backtest")
    best = results.iloc[0]

    col1, col2, col3 = st.columns(3)
    col1.metric("Grid Step", f"${best['grid_step']:.1f}")
    col2.metric("Max Positions", int(best["max_positions"]))
    col3.metric("TP %", f"{best['tp_pct']:.0f}%")

    with st.spinner("Running best backtest..."):
        r = run_backtest(
            df,
            grid_step        = float(best["grid_step"]),
            max_positions    = int(best["max_positions"]),
            tp_pct           = float(best["tp_pct"]),
            max_dd_pct       = float(best["max_dd_pct"]),
            stop_bot_pct     = stop_bot_pct,
            balance_per      = balance_per,
            min_lot          = 0.01,
            max_lot          = 2.00,
            start_hour_gmt7  = int(best["start_hour_gmt7"]),
            end_hour_gmt7    = int(best["end_hour_gmt7"]),
            spread_usd       = spread_usd,
            init_balance     = init_balance,
            force_close_min  = int(force_close_min),
            no_new_order_min = int(no_new_order_min),
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Final Balance",
              f"${r['final_balance']:.2f}",
              f"{r['net_gain_pct']:+.1f}%")
    c2.metric("Daily Return",
              f"{r['daily_ret_pct']:.4f}%/day",
              f"{r['total_days']} days")
    c3.metric("Win Rate",
              f"{r['win_rate']:.1f}%",
              f"{r['n_cycles']} cycles")
    c4.metric("Max Drawdown", f"{r['actual_max_dd_pct']:.1f}%")

    c5, c6, c7 = st.columns(3)
    c5.metric("Avg Positions/Cycle", f"{r['avg_n_pos']:.1f}")
    c6.metric("Max Depth Reached", r["max_depth"])
    c7.metric("Bot Halted?", "YES" if r["halted"] else "NO")

    if r["halted"]:
        st.warning("Bot was permanently halted (balance fell below stop threshold).")

    # ─── Martingale Risk Analysis ───────────────────────────────────────
    st.subheader("Martingale Risk Analysis")
    gs   = float(best["grid_step"])
    lot  = 0.01  # minimum lot (at $100 balance)
    max_p= int(best["max_positions"])

    risk_data = []
    cumulative_exposure = 0.0
    for depth in range(1, max_p + 1):
        entry_offset  = (depth - 1) * gs
        position_loss = entry_offset * lot * 100  # loss if price stays at add level
        cumulative_exposure += lot * 100 * gs
        tp_needed     = float(best["tp_pct"]) / 100 * init_balance
        # Price rise needed from avg entry to hit TP
        avg_entry_offset = (depth - 1) * gs / 2.0  # avg offset from first entry
        price_rise_needed = tp_needed / (lot * 100 * depth)
        risk_data.append({
            "Depth": depth,
            "Last Entry Drop (USD)": f"${entry_offset:.1f}",
            "Price Rise to TP (USD)": f"${price_rise_needed:.2f}",
            "Total Spread Cost": f"${depth * spread_usd:.2f}",
        })

    st.dataframe(pd.DataFrame(risk_data), use_container_width=True)

    # ─── Parameter Sensitivity ─────────────────────────────────────────
    if len(results) > 1:
        st.subheader("Parameter Sensitivity (vs daily_ret_pct)")
        for param in ["grid_step", "tp_pct", "max_positions"]:
            if param in results.columns:
                pivot = results.groupby(param)["daily_ret_pct"].mean().reset_index()
                pivot.columns = [param, "avg_daily_ret"]
                st.write(f"**{param}:**")
                st.dataframe(
                    pivot.style.format({"avg_daily_ret": "{:.4f}%"}),
                    use_container_width=True,
                )

    # ─── Save Results ───────────────────────────────────────────────────
    out_path = os.path.join(os.path.dirname(__file__), "output_gold_p1.csv")
    results.to_csv(out_path, index=False)
    st.success(f"Full results saved to `output_gold_p1.csv` ({len(results)} rows)")
