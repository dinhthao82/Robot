"""
MeanRevScalper v2 — Streamlit App
Exness Pro | EUR/USD | $100 | Day Trade | No Overnight
Strategy: BB + RSI Mean Reversion | TP=BB_mid | Breakeven stop | ATR gate
"""

import json
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from itertools import product as iproduct

from engine import SYMBOL_CONFIG, fetch_data, run_backtest, optimize, daily_stats, calc_lot

# ── Page ──────────────────────────────────────────────────────────
st.set_page_config(page_title="MeanRevScalper v2", page_icon="📈", layout="wide")
st.title("📈 MeanRevScalper v2 — BB + RSI Reversal")
st.caption("Exness Pro · EUR/USD · $100 · Day Trade · TP=BB Mid · Breakeven · ATR Gate")

# ── Sidebar ───────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Cấu hình")

    symbol = st.selectbox("Symbol", list(SYMBOL_CONFIG.keys()), index=0)
    cfg    = SYMBOL_CONFIG[symbol]

    _today     = pd.Timestamp.today().normalize()
    _start_def = _today - pd.Timedelta(days=729)
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Từ ngày", _start_def)
    with col2:
        end_date = st.date_input("Đến ngày", _today)
    if (pd.Timestamp.today() - pd.Timestamp(str(start_date))).days > 729:
        st.warning("⚠️ yfinance chỉ hỗ trợ 1H trong ~730 ngày gần nhất.")

    st.divider()
    st.subheader("💰 Tài khoản")
    balance_init    = st.number_input("Balance ($)",         value=100.0, step=50.0,  min_value=10.0)
    spread_pips     = st.number_input("Spread (pip)",        value=0.7,   step=0.1,   min_value=0.0)
    profit_lock_pct = st.number_input("Profit lock (%/day)", value=3.0,   step=0.5,   min_value=1.0)
    hard_stop_pct   = st.number_input("Hard stop (%/day)",   value=5.0,   step=0.5,   min_value=1.0)

    lot_now = calc_lot(balance_init)
    st.caption(
        f"Lot = **{lot_now:.2f}** · 1 pip = **${lot_now*cfg['pip_usd']:.2f}** · "
        f"Cần/day 2% = **${balance_init*0.02:.2f}** · 3% = **${balance_init*0.03:.2f}**"
    )

    st.divider()
    st.subheader("🔍 Grid Search")

    tp_mode = st.radio("TP mode", ["bb_mid (SMA — recom)", "atr (ATR×)"],
                       index=0, help="bb_mid = TP tại BB middle, thường đạt hơn ATR×")
    tp_mode_val = "bb_mid" if tp_mode.startswith("bb_mid") else "atr"

    col1, col2 = st.columns(2)
    with col1:
        bb_period_sel    = st.multiselect("BB Period",      [10, 14, 20, 30],   default=[20])
        rsi_period_sel   = st.multiselect("RSI Period",     [7, 10, 14],        default=[14])
        atr_sl_sel       = st.multiselect("ATR SL×",        [0.4, 0.5, 0.6, 0.7, 1.0], default=[0.5, 0.6])
        tbegin_sel       = st.multiselect("TBegin GMT+7",   list(range(13,20)), default=[15, 16])
    with col2:
        bb_std_sel       = st.multiselect("BB Std",         [1.5, 2.0, 2.5],    default=[1.5])
        rsi_os_sel       = st.multiselect("RSI OS thresh",  [25, 30, 35, 40],   default=[35])
        be_ratio_sel     = st.multiselect("Breakeven ratio",[0.3, 0.4, 0.5, 0.7], default=[0.3, 0.5])
        tend_sel         = st.multiselect("TEnd GMT+7",     list(range(19,23)), default=[21])

    trend_ema_sel = st.multiselect(
        "Trend EMA slope (0=off)",
        [0, 20, 50, 100, 200],
        default=[50],
        help="BUY chỉ khi EMA đang tăng, SELL khi EMA đang giảm. Cải thiện WR đáng kể."
    )
    min_atr_sel = st.multiselect("Min ATR (pip)",  [3.0, 5.0, 7.0, 10.0], default=[5.0, 7.0])
    if tp_mode_val == "atr":
        atr_tp_sel = st.multiselect("ATR TP×", [1.0, 1.5, 2.0, 2.5], default=[1.5, 2.0])
    else:
        atr_tp_sel = [1.5]

    col3, col4 = st.columns(2)
    with col3:
        max_trades_day = st.slider("Max trades/day", 1, 6, 5)
    with col4:
        sl_cooldown    = st.slider("SL cooldown (bars)", 0, 3, 0)
    max_atr_pips_val = st.slider("Max ATR (pip) — news filter", 15.0, 50.0, 25.0, 5.0)

    st.divider()
    run_btn = st.button("🚀 Chạy Optimizer", type="primary", use_container_width=True)

# ── Info khi chưa chạy ────────────────────────────────────────────
if not run_btn:
    st.info(
        "**Chiến thuật v2 — 4 cải tiến chính so với EMA Trend Follower cũ:**\n\n"
        "1. **TP = BB Middle (SMA)** — target tự nhiên của mean reversion, "
        "gần hơn và thực tế hơn ATR×2\n"
        "2. **Breakeven stop** — sau khi giá đi `be_ratio × TP_dist`, "
        "dời SL về entry → loại bỏ full-SL loss\n"
        "3. **RSI turning filter** — chỉ vào khi RSI bắt đầu quay chiều "
        "(không vào khi RSI vẫn tiếp tục đi sâu hơn)\n"
        "4. **ATR gate** — skip nếu ATR < 5 pip (flat) hoặc > 25 pip (news spike)\n\n"
        "👈 Nhấn **Chạy Optimizer** để bắt đầu."
    )

    # Compound preview @ 2%
    rows, bal = [], balance_init
    for m in range(1, 13):
        for _ in range(20):
            bal *= 1.02
        rows.append({"Tháng": m, "Balance ($)": round(bal, 2),
                     "Lot": calc_lot(bal),
                     "1 pip ($)": round(calc_lot(bal)*cfg["pip_usd"], 2)})
    st.subheader("📊 Lãi kép dự kiến @ 2%/ngày")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.stop()

# ── Fetch data ────────────────────────────────────────────────────
with st.spinner(f"📥 Tải {symbol}..."):
    try:
        df, sym_cfg = fetch_data(symbol, str(start_date), str(end_date))
    except Exception as e:
        st.error(f"❌ {e}"); st.stop()

n_days = (df.index[-1] - df.index[0]).days
st.success(f"✅ {len(df):,} bars | {df.index[0].date()} → {df.index[-1].date()} ({n_days} ngày)")

# ── Combo count ───────────────────────────────────────────────────
total_combos = sum(
    1 for bp,bs,rp,ro,asl,tb,te,be,ma,te_ema,atp
    in iproduct(bb_period_sel, bb_std_sel, rsi_period_sel, rsi_os_sel,
                atr_sl_sel, tbegin_sel, tend_sel,
                be_ratio_sel, min_atr_sel, trend_ema_sel, atr_tp_sel)
    if tb < te
)
st.info(
    f"🔢 Combos: **{total_combos:,}** "
    f"(BB×{len(bb_period_sel)} · Std×{len(bb_std_sel)} · "
    f"RSI×{len(rsi_period_sel)} · OS×{len(rsi_os_sel)} · "
    f"SL×{len(atr_sl_sel)} · BE×{len(be_ratio_sel)} · "
    f"EMA×{len(trend_ema_sel)} · ATR×{len(min_atr_sel)} · "
    f"Session×{len(tbegin_sel)}×{len(tend_sel)})"
)

# ── Optimize ──────────────────────────────────────────────────────
prog = st.progress(0, text="Bắt đầu...")

def on_progress(done, total):
    pct = int(done / total * 100)
    prog.progress(pct, text=f"Tối ưu... {done:,}/{total:,} ({pct}%)")

with st.spinner("Grid search đang chạy..."):
    results_df = optimize(
        df,
        pip_size         = sym_cfg["pip"],
        pip_usd_std      = sym_cfg["pip_usd"],
        bb_period_list   = bb_period_sel,
        bb_std_list      = bb_std_sel,
        rsi_period_list  = rsi_period_sel,
        rsi_os_list      = rsi_os_sel,
        atr_sl_list      = atr_sl_sel,
        tbegin_list      = tbegin_sel,
        tend_list        = tend_sel,
        be_ratio_list    = be_ratio_sel,
        min_atr_pips_list= min_atr_sel,
        trend_ema_list   = trend_ema_sel,
        tp_mode          = tp_mode_val,
        atr_tp_list      = atr_tp_sel,
        balance_init     = balance_init,
        max_trades_day   = max_trades_day,
        sl_cooldown      = sl_cooldown,
        spread_pips      = spread_pips,
        profit_lock_pct  = profit_lock_pct,
        hard_stop_pct    = hard_stop_pct,
        max_atr_pips     = max_atr_pips_val,
        progress_cb      = on_progress,
    )
prog.empty()

if results_df.empty:
    st.error("❌ Không có kết quả. Thử mở rộng tham số hoặc thay đổi ngày."); st.stop()

# ── Best params ───────────────────────────────────────────────────
best = results_df.iloc[0]

st.subheader("🏆 Best Params")
cols_p = st.columns(9)
cols_p[0].metric("BB",         f"{int(best['BB_period'])},{best['BB_std']}")
cols_p[1].metric("RSI",        f"{int(best['RSI_period'])}")
cols_p[2].metric("OS/OB",      f"{best['RSI_os']:.0f}/{best['RSI_ob']:.0f}")
cols_p[3].metric("ATR SL×",    f"{best['ATR_SL']}")
cols_p[4].metric("BE ratio",   f"{best['BE_ratio']}")
cols_p[5].metric("Min ATR pip",f"{best['Min_ATR_pips']}")
cols_p[6].metric("Session",    f"{int(best['TBegin']):02d}–{int(best['TEnd']):02d}")
cols_p[7].metric("Win Rate",   f"{best['Win_rate']*100:.1f}%",
                  "✅" if best["Win_rate"] >= 0.50 else "⚠️")
cols_p[8].metric("Daily Ret",  f"{best['Daily_ret_pct']:.3f}%",
                  "✅" if best['Daily_ret_pct'] >= 2.0 else "⚠️ <2%")

# ── Best backtest ─────────────────────────────────────────────────
with st.spinner("Backtest best params..."):
    best_cyc, best_eq = run_backtest(
        df,
        bb_period       = int(best["BB_period"]),
        bb_std          = float(best["BB_std"]),
        rsi_period      = int(best["RSI_period"]),
        rsi_os          = float(best["RSI_os"]),
        rsi_ob          = float(best["RSI_ob"]),
        atr_sl          = float(best["ATR_SL"]),
        tp_mode         = tp_mode_val,
        atr_tp          = float(best.get("ATR_TP", 1.5)),
        be_ratio        = float(best["BE_ratio"]),
        trend_ema       = int(best.get("Trend_EMA", 0)),
        min_atr_pips    = float(best["Min_ATR_pips"]),
        max_atr_pips    = max_atr_pips_val,
        tbegin          = int(best["TBegin"]),
        tend            = int(best["TEnd"]),
        balance_init    = balance_init,
        pip_size        = sym_cfg["pip"],
        pip_usd_std     = sym_cfg["pip_usd"],
        max_trades_day  = max_trades_day,
        sl_cooldown     = sl_cooldown,
        spread_pips     = spread_pips,
        profit_lock_pct = profit_lock_pct,
        hard_stop_pct   = hard_stop_pct,
    )

# ── Tabs ──────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Equity", "📅 Daily P&L", "🔥 Heatmap", "📋 All Results", "💾 Export"
])

# ── Tab 1: Equity ─────────────────────────────────────────────────
with tab1:
    if not best_eq.empty:
        peak = best_eq["equity"].cummax()
        dd   = (peak - best_eq["equity"]) / peak * 100

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=best_eq["dt"], y=best_eq["equity"],
            name="Equity", line=dict(color="#2196F3", width=1.5),
            fill="tozeroy", fillcolor="rgba(33,150,243,0.06)"))
        fig.add_trace(go.Scatter(x=best_eq["dt"], y=best_eq["balance"],
            name="Balance", line=dict(color="#4CAF50", width=1, dash="dash")))
        fig.add_trace(go.Scatter(x=best_eq["dt"], y=-dd, name="DD%", yaxis="y2",
            line=dict(color="rgba(244,67,54,0.6)", width=1),
            fill="tozeroy", fillcolor="rgba(244,67,54,0.07)"))
        fig.add_hline(y=balance_init, line_dash="dot", line_color="gray", annotation_text="Start")
        fig.update_layout(
            title=(f"BB({int(best['BB_period'])},{best['BB_std']}) "
                   f"RSI({int(best['RSI_period'])}) OS={best['RSI_os']:.0f} "
                   f"SL×{best['ATR_SL']} BE={best['BE_ratio']} "
                   f"ATR≥{best['Min_ATR_pips']}pip  "
                   f"{int(best['TBegin']):02d}–{int(best['TEnd']):02d} GMT+7"),
            height=430,
            yaxis=dict(title="USD"),
            yaxis2=dict(title="DD%", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

    if not best_cyc.empty:
        # Reason breakdown
        rc = best_cyc["reason"].value_counts()
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric("TP",       rc.get("TP",0))
        c2.metric("SL",       rc.get("SL",0))
        c3.metric("BE",       rc.get("BE",0), "✅ breakeven saved")
        c4.metric("TEnd",     rc.get("TEnd",0))
        c5.metric("HardStop", rc.get("HardStop",0))

        color_map = {"TP":"#26a69a","SL":"#ef5350","BE":"#FF9800",
                     "TEnd":"#90A4AE","HardStop":"#9C27B0","EOD":"#607D8B"}
        fig_sc = px.scatter(
            best_cyc.reset_index(), x="open_dt", y="profit",
            color="reason", color_discrete_map=color_map,
            title="P&L mỗi chu kỳ",
            labels={"profit":"Profit (USD)","open_dt":"Mở lệnh"},
            height=280,
        )
        fig_sc.add_hline(y=0, line_dash="dash", line_color="gray")
        st.plotly_chart(fig_sc, use_container_width=True)

# ── Tab 2: Daily P&L ──────────────────────────────────────────────
with tab2:
    if not best_cyc.empty:
        d = daily_stats(best_cyc, balance_init)
        if not d.empty:
            pos2 = (d["daily_pct"] >= 2.0).sum()
            pos3 = (d["daily_pct"] >= 3.0).sum()
            neg  = (d["daily_pct"] < 0).sum()
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("Ngày ≥2%",     f"{pos2}/{len(d)}", f"{pos2/len(d)*100:.0f}%")
            c2.metric("Ngày ≥3%",     f"{pos3}/{len(d)}", f"{pos3/len(d)*100:.0f}%")
            c3.metric("Ngày thua",    neg)
            c4.metric("Avg daily",    f"{d['daily_pct'].mean():.2f}%")
            c5.metric("Balance cuối", f"${best_cyc['balance_end'].iloc[-1]:.2f}")

            colors = ["#26a69a" if v >= 0 else "#ef5350" for v in d["daily_pct"]]
            fig_d = go.Figure(go.Bar(x=d["date"].astype(str), y=d["daily_pct"],
                                    marker_color=colors))
            fig_d.add_hline(y=2, line_dash="dot",  line_color="#4CAF50", annotation_text="2%")
            fig_d.add_hline(y=3, line_dash="dash", line_color="#FF9800", annotation_text="3%")
            fig_d.add_hline(y=-5,line_dash="dash", line_color="#ef5350", annotation_text="-5%")
            fig_d.update_layout(title="Daily P&L %", height=380,
                                yaxis_title="%", xaxis_title="")
            st.plotly_chart(fig_d, use_container_width=True)

            d_s = d.copy()
            d_s["daily_pct"] = d_s["daily_pct"].round(2)
            def _cp(v):
                if not isinstance(v,(int,float)): return ""
                if v>=3: return "background-color:#c8e6c9;color:#1b5e20"
                if v>=2: return "background-color:#dcedc8;color:#33691e"
                if v>=0: return "background-color:#fff9c4;color:#f57f17"
                return "background-color:#ffcdd2;color:#b71c1c"
            st.dataframe(d_s.style.map(_cp, subset=["daily_pct"]),
                         use_container_width=True, height=350, hide_index=True)

# ── Tab 3: Heatmaps ───────────────────────────────────────────────
with tab3:
    col1,col2 = st.columns(2)
    with col1:
        hm = results_df.groupby(["BB_period","BB_std"])["Daily_ret_pct"].max().reset_index()
        p  = hm.pivot(index="BB_std", columns="BB_period", values="Daily_ret_pct")
        st.plotly_chart(px.imshow(p, title="Daily% : BB_period × BB_std",
            color_continuous_scale="RdYlGn", aspect="auto",
            labels={"x":"BB Period","y":"BB Std","color":"Daily%"}
        ).update_layout(height=350), use_container_width=True)
    with col2:
        hm2 = results_df.groupby(["RSI_os","ATR_SL"])["Daily_ret_pct"].max().reset_index()
        p2  = hm2.pivot(index="ATR_SL", columns="RSI_os", values="Daily_ret_pct")
        st.plotly_chart(px.imshow(p2, title="Daily% : RSI_os × ATR_SL",
            color_continuous_scale="RdYlGn", aspect="auto",
            labels={"x":"RSI OS","y":"ATR SL×","color":"Daily%"}
        ).update_layout(height=350), use_container_width=True)

    col3,col4 = st.columns(2)
    with col3:
        hm3 = results_df.groupby(["BE_ratio","Min_ATR_pips"])["Daily_ret_pct"].max().reset_index()
        p3  = hm3.pivot(index="Min_ATR_pips", columns="BE_ratio", values="Daily_ret_pct")
        st.plotly_chart(px.imshow(p3, title="Daily% : BE_ratio × Min_ATR_pips",
            color_continuous_scale="RdYlGn", aspect="auto",
            labels={"x":"BE ratio","y":"Min ATR pip","color":"Daily%"}
        ).update_layout(height=350), use_container_width=True)
    with col4:
        hm4 = results_df.groupby(["TBegin","TEnd"])["Daily_ret_pct"].max().reset_index()
        p4  = hm4.pivot(index="TEnd", columns="TBegin", values="Daily_ret_pct")
        st.plotly_chart(px.imshow(p4, title="Daily% : TBegin × TEnd GMT+7",
            color_continuous_scale="RdYlGn", aspect="auto",
            labels={"x":"TBegin","y":"TEnd","color":"Daily%"}
        ).update_layout(height=350), use_container_width=True)

# ── Tab 4: All results ────────────────────────────────────────────
with tab4:
    c1,c2,c3 = st.columns(3)
    with c1: min_d  = st.slider("Min daily%", 0.0, 10.0, 0.0, 0.5)
    with c2: max_dd = st.slider("Max DD%",    0.0, 30.0, 30.0, 1.0)
    with c3: min_wr = st.slider("Min WR%",    0.0, 100.0, 0.0, 5.0)

    filt = results_df[
        (results_df["Daily_ret_pct"] >= min_d) &
        (results_df["Max_DD_pct"]    <= max_dd) &
        (results_df["Win_rate"]      >= min_wr/100)
    ]
    st.caption(f"Hiển thị {len(filt):,} / {len(results_df):,}")

    show = filt.head(200).copy()
    show["TBegin"] = show["TBegin"].apply(lambda h: f"{int(h):02d}:00")
    show["TEnd"]   = show["TEnd"].apply(lambda h:   f"{int(h):02d}:00")
    show["Win_rate"]      = (show["Win_rate"]*100).round(1)
    show["Daily_ret_pct"] = show["Daily_ret_pct"].round(3)
    show["Max_DD_pct"]    = show["Max_DD_pct"].round(2)

    def _cr(v):
        if not isinstance(v,(int,float)): return ""
        if v>=3:  return "background-color:#c8e6c9;color:#1b5e20"
        if v>=2:  return "background-color:#dcedc8;color:#33691e"
        if v>=0:  return "background-color:#fff9c4;color:#f57f17"
        return "background-color:#ffcdd2;color:#b71c1c"
    def _cd(v):
        if not isinstance(v,(int,float)): return ""
        if v<=5:  return "background-color:#c8e6c9;color:#1b5e20"
        if v<=10: return "background-color:#fff9c4;color:#f57f17"
        return "background-color:#ffcdd2;color:#b71c1c"

    st.dataframe(
        show.style.map(_cr, subset=["Daily_ret_pct","Win_rate"]).map(_cd, subset=["Max_DD_pct"]),
        use_container_width=True, height=500, hide_index=True,
    )

# ── Tab 5: Export ─────────────────────────────────────────────────
with tab5:
    rec = {
        "generated_at":    pd.Timestamp.now().isoformat(),
        "strategy":        "BB+RSI MeanRev v2 — TP=BB_mid, Breakeven, ATR gate",
        "broker":          "Exness Pro",
        "symbol":          symbol,
        "balance_init":    balance_init,
        "spread_pips":     spread_pips,
        "profit_lock_pct": profit_lock_pct,
        "hard_stop_pct":   hard_stop_pct,
        "tp_mode":         tp_mode_val,
        "test_period":     f"{start_date} → {end_date}",
        "best_params": {
            "BB_period":     int(best["BB_period"]),
            "BB_std":        float(best["BB_std"]),
            "RSI_period":    int(best["RSI_period"]),
            "RSI_os":        float(best["RSI_os"]),
            "RSI_ob":        float(best["RSI_ob"]),
            "ATR_SL":        float(best["ATR_SL"]),
            "BE_ratio":      float(best["BE_ratio"]),
            "Min_ATR_pips":  float(best["Min_ATR_pips"]),
            "TBegin":        int(best["TBegin"]),
            "TEnd":          int(best["TEnd"]),
            "win_rate":      float(best["Win_rate"]),
            "daily_ret_pct": float(best["Daily_ret_pct"]),
            "max_dd_pct":    float(best["Max_DD_pct"]),
            "score":         float(best["Score"]),
        },
        "top5": results_df.head(5).to_dict(orient="records"),
    }

    c1,c2,c3 = st.columns(3)
    with c1:
        st.download_button("⬇️ best_params.json",
            json.dumps(rec, indent=2, default=str),
            "best_params_mrev2.json","application/json", use_container_width=True)
    with c2:
        st.download_button("⬇️ all_results.csv",
            results_df.to_csv(index=False),
            "results_mrev2.csv","text/csv", use_container_width=True)
    with c3:
        if not best_cyc.empty:
            st.download_button("⬇️ cycles_best.csv",
                best_cyc.to_csv(index=False),
                "cycles_best_mrev2.csv","text/csv", use_container_width=True)

    st.code(json.dumps(rec["best_params"], indent=2), language="json")
    st.caption("Copy giá trị vào EA MetaTrader 5 — Exness Pro.")
