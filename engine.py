"""
MeanRevScalper Engine — v3
Strategy: Bollinger Band + RSI Mean Reversion | Day Trade | No Overnight
────────────────────────────────────────────────────────────────────────
Root-cause analysis từ log cũ:
  • Chỉ 1.1 lệnh/ngày (max=2, direction-lock) → chính là nguyên nhân PLN thấp
  • Cần 3+ lệnh/ngày: WR=60% x3 = 2.33 USD/day = 2.33%/day
  • RR ratio cũ 1.28:1 với 50% WR → gần breakeven
  • 48% ngày thua → cần breakeven mechanism

Cải tiến v3:
  1. Không có direction lock — BUY và SELL độc lập trong cùng ngày
  2. max_trades_day mặc định 4 (thay vì 2)
  3. min_tp_pips / max_sl_pips — cố định RR ratio thực tế
  4. Breakeven stop tại be_ratio × TP_dist
  5. RSI turning filter — chỉ vào khi RSI đang quay chiều
  6. ATR gate — bỏ flat day và news spike
  7. BB penetration filter — chỉ vào khi giá thực sự thủng band
     (không chỉ chạm mép ngoài)
  8. TP = BB middle (SMA) — natural reversion target
"""

import numpy as np
import pandas as pd
import yfinance as yf
from itertools import product

# ── Symbol config ─────────────────────────────────────────────────
SYMBOL_CONFIG = {
    "EURUSD": {"yf": "EURUSD=X", "pip": 0.0001, "pip_usd": 10.0, "desc": "Euro / USD"},
    "GBPUSD": {"yf": "GBPUSD=X", "pip": 0.0001, "pip_usd": 10.0, "desc": "GBP / USD"},
    "USDJPY": {"yf": "USDJPY=X", "pip": 0.01,   "pip_usd": 9.1,  "desc": "USD / JPY"},
    "AUDUSD": {"yf": "AUDUSD=X", "pip": 0.0001, "pip_usd": 10.0, "desc": "AUD / USD"},
    "XAUUSD": {"yf": "GC=F",     "pip": 0.1,    "pip_usd": 1.0,  "desc": "Gold / USD"},
    "BTCUSD": {"yf": "BTC-USD",  "pip": 1.0,    "pip_usd": 0.01, "desc": "Bitcoin / USD"},
}

# ── Data ──────────────────────────────────────────────────────────
def fetch_data(symbol: str, start: str, end: str) -> tuple[pd.DataFrame, dict]:
    cfg = SYMBOL_CONFIG.get(symbol.upper())
    if cfg is None:
        cfg = {"yf": symbol, "pip": 0.0001, "pip_usd": 10.0, "desc": symbol}
    df = yf.download(cfg["yf"], start=start, end=end,
                     interval="1h", auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"No data for {symbol}. yfinance only supports 1H within ~730 days.")
    df = df[["Open","High","Low","Close"]].copy()
    df.columns = ["open","high","low","close"]
    df.index = pd.to_datetime(df.index)
    if df.index.tzinfo is not None:
        df.index = df.index.tz_convert("Asia/Bangkok").tz_localize(None)
    else:
        df.index = df.index + pd.Timedelta(hours=7)
    return df.dropna(), cfg


# ── Lot formula ───────────────────────────────────────────────────
def calc_lot(balance: float) -> float:
    return max(0.01, round(balance / 10000, 2))


# ── Indicators ────────────────────────────────────────────────────
def _indicators(close, high, low, bb_period, bb_std_mult, rsi_period, atr_period=14):
    c = pd.Series(close)

    # Bollinger Bands
    sma       = c.rolling(bb_period, min_periods=bb_period).mean()
    std       = c.rolling(bb_period, min_periods=bb_period).std(ddof=0)
    upper_bb  = (sma + bb_std_mult * std).values
    middle_bb = sma.values
    lower_bb  = (sma - bb_std_mult * std).values
    bb_width  = (upper_bb - lower_bb)          # full band width in price units

    # RSI (Wilder)
    delta    = c.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1.0/rsi_period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1.0/rsi_period, adjust=False).mean()
    rs       = avg_gain / (avg_loss + 1e-9)
    rsi      = (100.0 - 100.0 / (1.0 + rs)).values

    # ATR
    n     = len(close)
    tr    = np.empty(n); tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:]-low[1:],
              np.maximum(np.abs(high[1:]-close[:-1]),
                         np.abs(low[1:] -close[:-1])))
    atr = pd.Series(tr).ewm(span=atr_period, adjust=False).mean().values

    return upper_bb, middle_bb, lower_bb, bb_width, rsi, atr


def _mk(pos, dt, pnl, reason, bal):
    return {"open_dt": pos["open_dt"], "close_dt": dt,
            "profit": round(pnl,4), "reason": reason,
            "dir": pos["dir"], "balance_end": round(bal,4)}


# ── Core backtest ─────────────────────────────────────────────────
def run_backtest(
    df: pd.DataFrame,
    bb_period: int      = 20,
    bb_std: float       = 1.5,      # tighter bands → closer TP → more achievable
    rsi_period: int     = 14,
    rsi_os: float       = 30.0,
    rsi_ob: float       = 70.0,
    atr_sl: float       = 0.7,
    atr_period: int     = 14,
    # TP mode
    tp_mode: str        = "bb_mid", # "bb_mid" = SMA | "atr" = ATR×
    atr_tp: float       = 1.5,
    # TP/SL clamp (pips) — fix RR ratio
    min_tp_pips: float  = 8.0,      # TP must be at least this far
    max_tp_pips: float  = 25.0,     # TP cap (avoid unreachable targets)
    min_sl_pips: float  = 4.0,      # SL floor (avoid noise SL)
    max_sl_pips: float  = 15.0,     # SL cap (avoid catastrophic loss)
    # Breakeven
    be_ratio: float     = 0.5,
    # BB penetration filter — price must be this % of band_width below lower_bb
    bb_penetration: float = 0.0,    # 0=disable, 0.05=5% of band_width
    # Trend filter — "buy the dip in uptrend, sell the rally in downtrend"
    trend_ema: int      = 0,        # 0=disabled. Typical: 50 or 200 (1H EMA)
    # ATR gate (pip)
    min_atr_pips: float = 5.0,
    max_atr_pips: float = 25.0,
    # Session
    tbegin: int         = 14,
    tend: int           = 22,
    # Account
    balance_init: float = 100.0,
    pip_size: float     = 0.0001,
    pip_usd_std: float  = 10.0,
    max_trades_day: int = 4,        # key fix: was 2, now 4
    spread_pips: float  = 0.7,
    sl_cooldown: int    = 1,        # reduced: was 3, now 1
    # Risk
    profit_lock_pct: float = 3.0,
    hard_stop_pct: float   = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    BB+RSI Mean Reversion v3.
    No direction lock. max_trades=4. TP=BB_mid clamped to min/max pip.
    Breakeven + RSI turning + ATR gate + BB penetration filter.
    """
    close = df["close"].values.astype(float)
    high  = df["high"].values.astype(float)
    low   = df["low"].values.astype(float)
    index = df.index
    n     = len(df)

    upper_bb, middle_bb, lower_bb, bb_width, rsi_arr, atr_arr = _indicators(
        close, high, low, bb_period, bb_std, rsi_period, atr_period)

    # Trend EMA (optional) — "buy dip in uptrend / sell rally in downtrend"
    if trend_ema > 0:
        trend_ema_arr = pd.Series(close).ewm(span=trend_ema, adjust=False).mean().values
    else:
        trend_ema_arr = None

    warmup = max(bb_period, rsi_period, atr_period,
                 trend_ema if trend_ema > 0 else 0) + 5

    balance       = float(balance_init)
    cycles        = []
    eq_rows       = []
    position      = None
    daily_trades  = 0
    prev_date     = None
    day_start_bal = balance
    halt_date     = None
    bars_since_sl = sl_cooldown

    for i in range(warmup, n):
        dt    = index[i]
        price = close[i]
        h     = dt.hour
        date  = dt.date()

        # new day
        if date != prev_date:
            prev_date    = date
            daily_trades = 0
            if date != halt_date:
                day_start_bal = balance

        # floating P&L
        fp = 0.0
        if position is not None:
            diff = (price - position["entry"]) if position["dir"] == "buy" \
                   else (position["entry"] - price)
            fp = diff / pip_size * pip_usd_std * position["lot"]

        equity = balance + fp
        eq_rows.append((dt, equity, balance))

        if date == halt_date:
            continue

        # hard stop
        if day_start_bal > 0 and (day_start_bal - equity) / day_start_bal * 100 >= hard_stop_pct:
            if position is not None:
                balance += fp
                cycles.append(_mk(position, dt, fp, "HardStop", balance))
                position = None
            halt_date = date
            continue

        # hard close at TEnd
        if h >= tend:
            if position is not None:
                balance += fp
                cycles.append(_mk(position, dt, fp, "TEnd", balance))
                position = None
            continue

        if h < tbegin or balance < 5.0:
            continue

        bars_since_sl += 1

        # manage open position
        if position is not None:
            entry   = position["entry"]
            tp_dist = position["tp_dist"]

            # breakeven check
            if not position["be_triggered"] and tp_dist > 0:
                be_thr = be_ratio * tp_dist
                if position["dir"] == "buy" and high[i] >= entry + be_thr:
                    position["sl"] = entry + spread_pips * 0.3 * pip_size
                    position["be_triggered"] = True
                elif position["dir"] == "sell" and low[i] <= entry - be_thr:
                    position["sl"] = entry - spread_pips * 0.3 * pip_size
                    position["be_triggered"] = True

            tp_hit = sl_hit = False
            if position["dir"] == "buy":
                tp_hit = high[i] >= position["tp"]
                sl_hit = low[i]  <= position["sl"]
            else:
                tp_hit = low[i]  <= position["tp"]
                sl_hit = high[i] >= position["sl"]

            if tp_hit:
                cp  = position["tp"]
                pnl = ((cp-entry) if position["dir"]=="buy" else (entry-cp)) \
                      / pip_size * pip_usd_std * position["lot"]
                balance += pnl
                cycles.append(_mk(position, dt, pnl, "TP", balance))
                position = None
                continue

            if sl_hit:
                cp  = position["sl"]
                pnl = ((cp-entry) if position["dir"]=="buy" else (entry-cp)) \
                      / pip_size * pip_usd_std * position["lot"]
                balance += pnl
                reason = "BE" if position["be_triggered"] else "SL"
                cycles.append(_mk(position, dt, pnl, reason, balance))
                position = None
                if reason == "SL":
                    bars_since_sl = 0
                continue

        # profit lock
        if day_start_bal > 0 and (equity-day_start_bal)/day_start_bal*100 >= profit_lock_pct:
            continue

        # new entry
        if position is None and daily_trades < max_trades_day and bars_since_sl >= sl_cooldown and i >= 1:
            ub   = upper_bb[i]
            mb   = middle_bb[i]
            lb   = lower_bb[i]
            bw   = bb_width[i]
            rv   = rsi_arr[i]
            rv1  = rsi_arr[i-1]

            if np.isnan(ub) or np.isnan(mb) or np.isnan(lb) or np.isnan(rv) or np.isnan(rv1):
                continue

            cur_atr     = max(float(atr_arr[i]), pip_size * 3)
            cur_atr_pip = cur_atr / pip_size

            if cur_atr_pip < min_atr_pips or cur_atr_pip > max_atr_pips:
                continue

            direction = None

            # ── Signal logic ──────────────────────────────────
            # OR mode (default): BB touch OR RSI extreme (with SMA filter)
            # AND mode (strict): BB touch AND RSI extreme
            bb_buy   = price < lb                  # below lower band
            bb_sell  = price > ub                  # above upper band
            rsi_buy  = rv < rsi_os                 # RSI oversold
            rsi_sell = rv > rsi_ob                 # RSI overbought
            sma_buy  = price < mb                  # price below SMA (mean)
            sma_sell = price > mb                  # price above SMA (mean)

            # Trend EMA slope gate — buy when EMA rising, sell when EMA falling
            # Uses SLOPE not price-vs-EMA (avoids conflict with price < lower_bb)
            if trend_ema_arr is not None and i >= trend_ema:
                slope_lb     = max(1, trend_ema // 5)   # lookback for slope
                ema_now      = trend_ema_arr[i]
                ema_prev     = trend_ema_arr[i - slope_lb]
                in_uptrend   = ema_now >= ema_prev      # EMA rising  → buy dips
                in_downtrend = ema_now <= ema_prev      # EMA falling → sell rallies
            else:
                in_uptrend = in_downtrend = True  # warmup / disabled

            # OR logic: BB touch OR RSI extreme, gated by SMA + trend
            buy_ok  = in_uptrend   and sma_buy  and (bb_buy  or rsi_buy)
            sell_ok = in_downtrend and sma_sell and (bb_sell or rsi_sell)

            # BB penetration filter (optional — requires real BB penetration)
            if bb_penetration > 0 and not np.isnan(bw) and bw > 0:
                if buy_ok and bb_buy:
                    pen = abs(price - lb) / bw
                    if pen < bb_penetration:
                        buy_ok = rsi_buy and sma_buy  # fallback to RSI only
                if sell_ok and bb_sell:
                    pen = abs(price - ub) / bw
                    if pen < bb_penetration:
                        sell_ok = rsi_sell and sma_sell

            if buy_ok:
                direction = "buy"
            elif sell_ok:
                direction = "sell"
            else:
                continue

            lot         = calc_lot(balance)
            spread_cost = spread_pips * pip_usd_std * lot
            balance    -= spread_cost
            entry       = price

            # TP calculation
            if tp_mode == "bb_mid":
                raw_tp_dist = abs(mb - entry)
                # SKIP (not extend) if price too close to SMA — no real reversion room
                if raw_tp_dist < min_tp_pips * pip_size:
                    balance += spread_cost   # refund spread
                    continue
                # Only cap from above
                tp_dist = min(raw_tp_dist, max_tp_pips * pip_size)
            else:
                raw_tp_dist = atr_tp * cur_atr
                tp_dist = np.clip(raw_tp_dist,
                                  min_tp_pips * pip_size,
                                  max_tp_pips * pip_size)

            # SL: ATR-based + clamp
            raw_sl_dist = atr_sl * cur_atr
            sl_dist = np.clip(raw_sl_dist,
                              min_sl_pips * pip_size,
                              max_sl_pips * pip_size)

            if direction == "buy":
                tp = entry + tp_dist
                sl = entry - sl_dist
            else:
                tp = entry - tp_dist
                sl = entry + sl_dist

            position = {
                "dir": direction, "entry": entry,
                "lot": lot, "tp": tp, "sl": sl,
                "tp_dist": tp_dist, "be_triggered": False,
                "open_dt": dt,
            }
            daily_trades += 1

    # close at EOD
    if position is not None:
        diff = (close[-1]-position["entry"]) if position["dir"]=="buy" \
               else (position["entry"]-close[-1])
        fp = diff / pip_size * pip_usd_std * position["lot"]
        balance += fp
        cycles.append(_mk(position, index[-1], fp, "EOD", balance))

    cols      = ["open_dt","close_dt","profit","reason","dir","balance_end"]
    cycles_df = pd.DataFrame(cycles, columns=cols) if cycles else pd.DataFrame(columns=cols)
    eq_df     = pd.DataFrame(eq_rows, columns=["dt","equity","balance"])
    return cycles_df, eq_df


# ── Daily stats ───────────────────────────────────────────────────
def daily_stats(cycles_df: pd.DataFrame, balance_init: float) -> pd.DataFrame:
    if cycles_df.empty:
        return pd.DataFrame()
    df = cycles_df.copy()
    df["date"] = pd.to_datetime(df["close_dt"]).dt.date
    daily = df.groupby("date").agg(
        total_profit=("profit","sum"),
        n_trades=("profit","count"),
        win_trades=("profit", lambda x:(x>0).sum()),
    ).reset_index()
    daily["balance_end"] = (
        cycles_df.groupby(pd.to_datetime(cycles_df["close_dt"]).dt.date)["balance_end"]
        .last().values
    )
    daily["daily_pct"] = daily["total_profit"] / balance_init * 100
    return daily


def score_cycles(cycles_df: pd.DataFrame) -> float:
    if cycles_df.empty or len(cycles_df) < 3:
        return -1e9
    p   = cycles_df["profit"].values
    tot = p.sum()
    wr  = (p > 0).mean()
    sh  = p.mean() / (p.std() + 1e-9)
    n   = len(p)
    return tot * wr * np.log1p(n) * (1 + sh * 0.1)


# ── Optimizer ─────────────────────────────────────────────────────
def optimize(
    df: pd.DataFrame,
    pip_size: float,
    pip_usd_std: float,
    bb_period_list,
    bb_std_list,
    rsi_period_list,
    rsi_os_list,
    atr_sl_list,
    tbegin_list,
    tend_list,
    be_ratio_list        = (0.5,),
    min_atr_pips_list    = (5.0,),
    min_tp_pips_list     = (8.0,),
    bb_penetration_list  = (0.0,),
    trend_ema_list       = (0,),    # 0=off, 50, 100, 200
    tp_mode: str         = "bb_mid",
    atr_tp_list          = (1.5,),
    balance_init: float      = 100.0,
    max_trades_day: int      = 4,
    sl_cooldown: int         = 1,
    atr_period: int          = 14,
    spread_pips: float       = 0.7,
    profit_lock_pct: float   = 3.0,
    hard_stop_pct: float     = 5.0,
    max_atr_pips: float      = 25.0,
    max_tp_pips: float       = 25.0,
    min_sl_pips: float       = 4.0,
    max_sl_pips: float       = 15.0,
    progress_cb = None,
    result_cb   = None,
) -> pd.DataFrame:
    combos = [
        (bp, bs, rp, ro, asl, tb, te, be, ma, mtp, bpen, te_ema, atp)
        for bp, bs, rp, ro, asl, tb, te, be, ma, mtp, bpen, te_ema, atp
        in product(bb_period_list, bb_std_list, rsi_period_list, rsi_os_list,
                   atr_sl_list, tbegin_list, tend_list,
                   be_ratio_list, min_atr_pips_list, min_tp_pips_list,
                   bb_penetration_list, trend_ema_list, atr_tp_list)
        if tb < te
    ]

    results = []
    total   = len(combos)

    for idx, (bp, bs, rp, ro, asl, tb, te, be, ma, mtp, bpen, te_ema, atp) in enumerate(combos):
        rsi_ob_val = 100.0 - ro

        cyc, eq = run_backtest(
            df,
            bb_period=bp, bb_std=bs,
            rsi_period=rp, rsi_os=ro, rsi_ob=rsi_ob_val,
            atr_sl=asl, atr_period=atr_period,
            tp_mode=tp_mode, atr_tp=atp,
            min_tp_pips=mtp, max_tp_pips=max_tp_pips,
            min_sl_pips=min_sl_pips, max_sl_pips=max_sl_pips,
            be_ratio=be,
            bb_penetration=bpen,
            trend_ema=te_ema,
            min_atr_pips=ma, max_atr_pips=max_atr_pips,
            tbegin=tb, tend=te,
            balance_init=balance_init,
            pip_size=pip_size, pip_usd_std=pip_usd_std,
            max_trades_day=max_trades_day,
            spread_pips=spread_pips,
            sl_cooldown=sl_cooldown,
            profit_lock_pct=profit_lock_pct,
            hard_stop_pct=hard_stop_pct,
        )

        if progress_cb:
            progress_cb(idx + 1, total)

        if cyc.empty or len(cyc) < 5:
            continue

        p   = cyc["profit"].values
        tot = p.sum()
        wr  = (p > 0).mean()
        avg = p.mean()
        sh  = avg / (p.std() + 1e-9)
        n   = len(p)
        sc  = tot * wr * np.log1p(n) * (1 + sh * 0.1)

        n_days    = max(1, (df.index[-1] - df.index[0]).days)
        daily_ret = (tot / balance_init / n_days) * 100

        if not eq.empty:
            eq["peak"] = eq["equity"].cummax()
            eq["dd"]   = (eq["peak"] - eq["equity"]) / eq["peak"].clip(lower=1e-9) * 100
            max_dd     = round(eq["dd"].max(), 2)
        else:
            max_dd = 0.0

        tp_cnt = (cyc["reason"] == "TP").sum()
        sl_cnt = (cyc["reason"] == "SL").sum()
        be_cnt = (cyc["reason"] == "BE").sum()
        te_cnt = (cyc["reason"] == "TEnd").sum()
        trades_per_day = round(n / max(1, n_days / 7 * 5), 2)  # trades per trading day

        row = {
            "BB_period":       bp,
            "BB_std":          bs,
            "RSI_period":      rp,
            "RSI_os":          ro,
            "RSI_ob":          round(rsi_ob_val, 1),
            "ATR_SL":          asl,
            "BE_ratio":        be,
            "Min_ATR_pips":    ma,
            "Min_TP_pips":     mtp,
            "BB_pen":          bpen,
            "Trend_EMA":       te_ema,
            "TP_mode":         tp_mode,
            "ATR_TP":          atp,
            "TBegin":          tb,
            "TEnd":            te,
            "N_trades":        n,
            "Trades_per_day":  trades_per_day,
            "N_TP":            tp_cnt,
            "N_SL":            sl_cnt,
            "N_BE":            be_cnt,
            "N_TEnd":          te_cnt,
            "Total_profit":    round(tot, 4),
            "Avg_profit":      round(avg, 4),
            "Daily_ret_pct":   round(daily_ret, 3),
            "Win_rate":        round(wr, 4),
            "Sharpe":          round(sh, 4),
            "Max_DD_pct":      max_dd,
            "Score":           round(sc, 4),
        }
        results.append(row)
        if result_cb:
            result_cb(len(results), row)

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("Score", ascending=False).reset_index(drop=True)
