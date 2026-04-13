"""
engine_gold.py — BUY-Only Martingale Grid Backtest for XAUUSD
Based on NewBotGold.mq5 (AutoProject V2 v1.10)

Strategy:
  - No open positions → open BUY at bar close (within trading window)
  - Positions open  → add BUY when price drops >= grid_step from lowest entry
  - TP: close ALL when total floating PnL >= tp_pct% of balance
  - Daily DD stop: halt today if floating loss >= max_dd_pct% of day-high equity
  - Permanent halt: stop forever if balance < stop_bot_pct% of start balance

Symbol: XAUUSD (Gold Futures via yfinance "GC=F")
  - 1 standard lot = 100 troy oz
  - 1 USD price move × 0.01 lot × 100 oz = $1.00 PnL
"""

import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np
from itertools import product as iproduct
from concurrent.futures import ProcessPoolExecutor

# ===== CONSTANTS =====
# Exness broker uses symbol "GOLD" (= spot XAU/USD).
# yfinance proxy: "GC=F" (COMEX futures) — prices match within ~$1-2.
# Spread calibrated from real Exness GOLD tester log:
#   avg=0.309 USD, median=0.300 USD per 0.01 lot (1,167 samples, 2024-04)
# Timeframes: "1h" → up to 2y history | "15m" → max 60 days (more accurate)
SYMBOL      = "GC=F"   # COMEX Gold Futures (proxy for Exness GOLD)
LOT_FACTOR  = 100      # oz per standard lot
INIT_BAL    = 100.0


# ===== DATA LOADING =====
def load_data(period: str = "2y", interval: str = "1h") -> pd.DataFrame:
    """Download OHLC data from yfinance, return UTC-naive DataFrame.
    interval='1h'  → up to 2 years history (use for optimization)
    interval='15m' → max 60 days (more accurate intrabar grid simulation)
    """
    df = yf.download(SYMBOL, period=period, interval=interval,
                     progress=False, auto_adjust=True)
    df.index = pd.to_datetime(df.index)
    if df.index.tzinfo is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)

    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df[["Open", "High", "Low", "Close"]].dropna()
    return df


# ===== LOT SIZING =====
def calc_lot(balance: float, balance_per: float,
             min_lot: float, max_lot: float) -> float:
    """Mirror MQL5 CalcLot: floor(balance / balance_per) * 0.01, clamped."""
    lot = np.floor(balance / balance_per) * 0.01
    lot = max(min_lot, min(max_lot, lot))
    return round(lot, 2)


# ===== CORE BACKTEST =====
def run_backtest(
    df: pd.DataFrame,
    grid_step: float       = 1.0,    # USD drop to add next BUY
    max_positions: int     = 10,     # max BUYs per cycle
    tp_pct: float          = 30.0,   # close all when floating >= tp_pct% of balance
    max_dd_pct: float      = 20.0,   # daily DD stop (% of day-high equity)
    stop_bot_pct: float    = 50.0,   # permanent halt if balance < X% of start
    balance_per: float     = 1000.0, # $X per 0.01 lot (lot scaling)
    min_lot: float         = 0.01,
    max_lot: float         = 2.00,
    start_hour_gmt7: int   = 8,      # trading window open (GMT+7)
    end_hour_gmt7: int     = 23,     # trading window close (GMT+7)
    spread_usd: float      = 0.30,   # spread cost (USD) per 0.01 lot (one-way, entry only)
    init_balance: float    = INIT_BAL,
    force_close_min: int   = 5,      # V2 Day Trading: force-close X min before session end
    no_new_order_min: int  = 10,     # V2 Day Trading: no new orders X min before session end
) -> dict:
    """
    Simulate the NewBotGold_V2_DayTrading strategy on OHLC bars.

    Spread model (matches MT5):
      Entry at ASK = bar_open + spread_price. Floating = (bid - ask_entry) × lot × 100.
      Spread paid ONCE at entry (no deduction at close). Matches MT5 buy-at-ask model.

    V2 Day Trading rules:
      - Force-close all positions force_close_min before session end
      - No new orders no_new_order_min before session end

    Intrabar order:
      1. EOD force-close check (bar open price)
      2. TP check (bar high)
      3. Grid adds (bar low) with re-TP after each add
      4. DD check (bar low)
    """

    def floating(entries, lot, price):
        """Floating PnL: bid price vs ASK entries (spread already in entry)."""
        if not entries:
            return 0.0
        return lot * LOT_FACTOR * sum(price - e for e in entries)

    def tp_price_for(entries, lot, target_pnl):
        """Bid price at which floating PnL = target_pnl."""
        n = len(entries)
        if n == 0 or lot == 0:
            return float("inf")
        return sum(entries) / n + target_pnl / (lot * LOT_FACTOR * n)

    def close_cycle(entries, lot, close_price, balance, cycles):
        """Realize PnL. Entries are at ASK — spread already factored in, no close deduction."""
        pnl = floating(entries, lot, close_price)
        balance += pnl
        cycles.append({"pnl": round(pnl, 4), "n_pos": len(entries), "win": pnl > 0})
        return balance, [], 0.0

    balance      = init_balance
    start_bal    = init_balance
    entries      = []   # ASK prices of open positions
    cycle_lot    = 0.0

    day_high_eq  = balance
    today_stop   = False
    last_date    = None
    bot_halted   = False

    cycles       = []
    eq_curve     = []

    opens  = df["Open"].values
    highs  = df["High"].values
    lows   = df["Low"].values
    closes = df["Close"].values
    times  = df.index

    # Precompute session end in minutes (GMT+7)
    end_min_gmt7       = end_hour_gmt7 * 60
    force_close_thresh = end_min_gmt7 - force_close_min
    no_new_order_thresh= end_min_gmt7 - no_new_order_min

    for i in range(len(df)):
        if bot_halted:
            break

        t       = times[i]
        b_open  = opens[i]
        b_high  = highs[i]
        b_low   = lows[i]
        b_close = closes[i]

        # Bar time → minutes since midnight, GMT+7
        t_min    = t.hour * 60 + (t.minute if hasattr(t, 'minute') else 0)
        bar_min_gmt7 = (t_min + 7 * 60) % (24 * 60)

        # ── New day ──────────────────────────────────────────────────────
        b_date = t.date()
        if b_date != last_date:
            today_stop  = False
            day_high_eq = balance + floating(entries, cycle_lot, b_open)
            last_date   = b_date

        # ── Permanent halt ───────────────────────────────────────────────
        if balance < start_bal * stop_bot_pct / 100.0:
            if entries:
                balance, entries, cycle_lot = close_cycle(
                    entries, cycle_lot, b_open, balance, cycles)
            bot_halted = True
            break

        # ── If today already stopped: track equity and skip ──────────────
        if today_stop:
            eq_curve.append(balance + floating(entries, cycle_lot, b_close))
            continue

        # ── 1. Update day_high from bar HIGH ─────────────────────────────
        if entries:
            eq_at_high = balance + floating(entries, cycle_lot, b_high)
            if eq_at_high > day_high_eq:
                day_high_eq = eq_at_high

        # ── 2. EOD force-close (V2 Day Trading) — at bar OPEN ────────────
        #    Mirrors MT5 CheckEndOfDay(): close all ForceCloseMin before end
        tp_hit = False
        if entries and bar_min_gmt7 >= force_close_thresh:
            balance, entries, cycle_lot = close_cycle(
                entries, cycle_lot, b_open, balance, cycles)
            if balance > day_high_eq:
                day_high_eq = balance
            today_stop = True   # no new orders after EOD close
            eq_curve.append(balance)
            continue

        # ── 3. TP check at bar HIGH ───────────────────────────────────────
        #    MT5: float >= balance × tp_pct%. Spread already in entry (ASK).
        if entries:
            tp_target = balance * tp_pct / 100.0
            tp_p = tp_price_for(entries, cycle_lot, tp_target)
            if b_high >= tp_p:
                balance, entries, cycle_lot = close_cycle(
                    entries, cycle_lot, tp_p, balance, cycles)
                if balance > day_high_eq:
                    day_high_eq = balance
                tp_hit = True

        if tp_hit:
            eq_curve.append(balance)
            continue   # new cycle opens next bar

        # ── 4. Trading window + grid logic ───────────────────────────────
        #    NoNewOrderMin: no new orders (open or add) X min before session end
        in_window = (start_hour_gmt7 * 60 <= bar_min_gmt7 < no_new_order_thresh)

        if in_window:
            sp = spread_usd / (cycle_lot * LOT_FACTOR) if cycle_lot > 0 else 0.0

            if not entries:
                # Open first position at ASK (bar open + spread in price units)
                cycle_lot = calc_lot(balance, balance_per, min_lot, max_lot)
                sp = spread_usd / (cycle_lot * LOT_FACTOR)
                entries.append(b_open + sp)   # entry at ASK, no balance deduction

                # Immediate TP check (price may spike same bar)
                tp_target = balance * tp_pct / 100.0
                tp_p = tp_price_for(entries, cycle_lot, tp_target)
                if b_high >= tp_p:
                    balance, entries, cycle_lot = close_cycle(
                        entries, cycle_lot, tp_p, balance, cycles)
                    if balance > day_high_eq:
                        day_high_eq = balance
                    tp_hit = True
                else:
                    # Grid adds on same bar (bar may dip from open)
                    while len(entries) < max_positions:
                        next_bid = min(entries) - grid_step
                        if b_low > next_bid:
                            break
                        entries.append(next_bid + sp)   # add at ASK
                        tp_target = balance * tp_pct / 100.0
                        tp_p = tp_price_for(entries, cycle_lot, tp_target)
                        if b_high >= tp_p:
                            balance, entries, cycle_lot = close_cycle(
                                entries, cycle_lot, tp_p, balance, cycles)
                            if balance > day_high_eq:
                                day_high_eq = balance
                            tp_hit = True
                            break
            else:
                # Add positions while price drops through grid levels
                while len(entries) < max_positions:
                    next_bid = min(entries) - grid_step
                    if b_low > next_bid:
                        break
                    entries.append(next_bid + sp)       # add at ASK
                    tp_target = balance * tp_pct / 100.0
                    tp_p = tp_price_for(entries, cycle_lot, tp_target)
                    if b_high >= tp_p:
                        balance, entries, cycle_lot = close_cycle(
                            entries, cycle_lot, tp_p, balance, cycles)
                        if balance > day_high_eq:
                            day_high_eq = balance
                        tp_hit = True
                        break

        # ── 5. DD check at bar LOW — intrabar simulation ─────────────────
        if entries and day_high_eq > 0 and not tp_hit:
            n            = len(entries)
            avg_entry    = sum(entries) / n
            eq_trigger   = day_high_eq * (1.0 - max_dd_pct / 100.0)
            needed_float = eq_trigger - balance
            trig_p       = avg_entry + needed_float / (n * cycle_lot * LOT_FACTOR)

            if trig_p >= b_low:
                today_stop = True
                close_p = trig_p if trig_p <= b_high else b_open
                balance, entries, cycle_lot = close_cycle(
                    entries, cycle_lot, close_p, balance, cycles)

        # ── 6. Equity curve at bar close ─────────────────────────────────
        eq = balance + floating(entries, cycle_lot, b_close)
        if eq > day_high_eq:
            day_high_eq = eq
        eq_curve.append(eq)

    # ── Force-close any open positions at last bar close ──────────────
    if entries:
        last_price = closes[-1]
        balance, entries, cycle_lot = close_cycle(
            entries, cycle_lot, last_price, balance, cycles)

    # ── Metrics ──────────────────────────────────────────────────────────
    total_days  = max(1, (df.index[-1] - df.index[0]).days)
    n_cyc       = len(cycles)
    wins        = sum(1 for c in cycles if c["win"])
    pnls        = [c["pnl"] for c in cycles]
    n_pos_list  = [c["n_pos"] for c in cycles]

    net_gain_pct = (balance - init_balance) / init_balance * 100.0
    daily_ret    = net_gain_pct / total_days

    if eq_curve:
        eq_arr   = np.array(eq_curve, dtype=float)
        peak_arr = np.maximum.accumulate(eq_arr)
        mask     = peak_arr > 0
        dd_arr   = np.where(mask, (peak_arr - eq_arr) / peak_arr * 100.0, 0.0)
        max_dd   = float(dd_arr.max())
    else:
        max_dd = 0.0

    # Compound daily return (CAGR equivalent)
    if balance > 0 and init_balance > 0 and total_days > 0:
        cagr_daily = (balance / init_balance) ** (1.0 / total_days) - 1.0
    else:
        cagr_daily = -1.0

    return {
        "final_balance":    round(balance, 2),
        "net_gain_pct":     round(net_gain_pct, 2),
        "daily_ret_pct":    round(daily_ret, 4),         # simple avg: total_gain/days
        "cagr_daily_pct":   round(cagr_daily * 100, 4),  # compound daily return
        "n_cycles":         n_cyc,
        "win_rate":         round(wins / n_cyc * 100, 1) if n_cyc else 0.0,
        "avg_pnl":          round(float(np.mean(pnls)), 3) if pnls else 0.0,
        "avg_n_pos":        round(float(np.mean(n_pos_list)), 2) if n_pos_list else 0.0,
        "actual_max_dd_pct": round(max_dd, 2),           # renamed to avoid collision
        "max_depth":        max(n_pos_list) if n_pos_list else 0,
        "total_days":       total_days,
        "halted":           bot_halted,
    }


# ===== OPTIMIZER =====
def _worker(args):
    df, params = args
    try:
        r = run_backtest(df, **params)
        r.update(params)
        r.pop("df", None)
        return r
    except Exception:
        return None


def optimize(
    df: pd.DataFrame,
    grid_step_list:     list = [1.0, 2.0],
    max_positions_list: list = [10, 15],
    tp_pct_list:        list = [20.0, 30.0],
    max_dd_pct_list:    list = [20.0],
    start_hour_list:    list = [8],
    end_hour_list:      list = [23],
    stop_bot_pct:       float = 50.0,
    balance_per:        float = 1000.0,
    min_lot:            float = 0.01,
    max_lot:            float = 2.00,
    spread_usd:         float = 0.30,
    init_balance:       float = INIT_BAL,
    force_close_min:    int   = 5,
    no_new_order_min:   int   = 10,
    max_workers:        int = 1,
    progress_cb=None,
) -> pd.DataFrame:
    """Run grid search over parameter combinations."""

    combos = [
        {
            "grid_step":         gs,
            "max_positions":     int(mp),
            "tp_pct":            tp,
            "max_dd_pct":        mdd,
            "start_hour_gmt7":   int(sh),
            "end_hour_gmt7":     int(eh),
            "stop_bot_pct":      stop_bot_pct,
            "balance_per":       balance_per,
            "min_lot":           min_lot,
            "max_lot":           max_lot,
            "spread_usd":        spread_usd,
            "init_balance":      init_balance,
            "force_close_min":   force_close_min,
            "no_new_order_min":  no_new_order_min,
        }
        for gs, mp, tp, mdd, sh, eh in iproduct(
            grid_step_list, max_positions_list, tp_pct_list,
            max_dd_pct_list, start_hour_list, end_hour_list,
        )
        if sh < eh
    ]

    args_list = [(df, c) for c in combos]
    results   = []
    done      = 0

    if max_workers and max_workers > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            for r in ex.map(_worker, args_list, chunksize=10):
                if r:
                    results.append(r)
                done += 1
                if progress_cb:
                    progress_cb(done, len(combos))
    else:
        for a in args_list:
            r = _worker(a)
            if r:
                results.append(r)
            done += 1
            if progress_cb:
                progress_cb(done, len(combos))

    if not results:
        return pd.DataFrame()

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values("cagr_daily_pct", ascending=False).reset_index(drop=True)
    return df_res


# ===== CLI QUICK TEST =====
if __name__ == "__main__":
    print("Loading XAUUSD data...")
    df = load_data(period="2y", interval="1h")
    print(f"Bars: {len(df)} | {df.index[0].date()} to {df.index[-1].date()}")

    print("\nRunning default backtest (grid=1.0, max_pos=10, tp=30%)...")
    r = run_backtest(df)
    for k, v in r.items():
        print(f"  {k:20s}: {v}")

    print("\nRunning small optimizer (9 combos)...")
    res = optimize(
        df,
        grid_step_list     = [1.0, 2.0, 3.0],
        max_positions_list = [10],
        tp_pct_list        = [20.0, 30.0, 40.0],
        max_dd_pct_list    = [20.0],
        start_hour_list    = [8],
        end_hour_list      = [23],
    )
    print(res[["cagr_daily_pct", "daily_ret_pct", "final_balance", "win_rate",
               "n_cycles", "avg_n_pos", "actual_max_dd_pct", "grid_step", "tp_pct"]].head())
