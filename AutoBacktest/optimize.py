#!/usr/bin/env python3
"""
ScalpingEURUSD – Parameter Optimizer
=====================================
Mục tiêu: WR > 70%, RR 1:1 (SL = TP)

Chiến lược tối ưu:
  - EMA21 / EMA50 trend filter
  - RSI momentum filter
  - [New] require_ema50_touch: chỉ vào lệnh khi giá chạm EMA50 (pullback sâu hơn)
  - [New] min_body_pts: lọc nến doji/indecision
  - [New] session_vn tối ưu (London/NY session peak)
  - SL = TP (true RR 1:1)
  - BE nhanh tại 50% SL

Usage:
  python optimize.py --data eurusd_m5.csv --start 2025-04-09 --end 2026-04-09

Requirements:
  pip install pandas numpy tabulate
"""

import argparse, sys, time, itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("pip install pandas numpy tabulate"); sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TAB = True
except:
    HAS_TAB = False

POINT = 0.00001
CONTRACT = 100_000.0

# ─────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────
def load_data(path: str) -> pd.DataFrame:
    """Load EURUSD M5 CSV (MT5 API format or MT5 export format)."""
    df = pd.read_csv(path)

    # MT5 Python API format: datetime,open,high,low,close,tick_volume
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
    # MT5 export format: <DATE>\t<TIME>\t<OPEN>...
    elif df.columns[0].startswith('<'):
        df.columns = [c.strip('<>').lower() for c in df.columns]
        df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'])
    else:
        df.columns = [c.lower() for c in df.columns]
        df['datetime'] = pd.to_datetime(df.iloc[:, 0])

    df = df[['datetime', 'open', 'high', 'low', 'close']].copy()
    df = df.dropna().sort_values('datetime').reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, fast: int = 21, slow: int = 50,
                       rsi_period: int = 14) -> pd.DataFrame:
    c = df['close']
    df['ema21']   = c.ewm(span=fast,  adjust=False).mean()
    df['ema50']   = c.ewm(span=slow,  adjust=False).mean()
    df['ema21_p'] = df['ema21'].shift(1)
    df['ema50_p'] = df['ema50'].shift(1)
    df['close_p'] = c.shift(1)
    df['open_p']  = df['open'].shift(1)
    # RSI Wilder smoothing (matches MT5)
    delta  = c.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    ag     = gain.ewm(alpha=1.0 / rsi_period, adjust=False).mean()
    al     = loss.ewm(alpha=1.0 / rsi_period, adjust=False).mean()
    rs     = ag / al.replace(0, np.nan)
    df['rsi'] = (100 - 100 / (1 + rs)).fillna(50.0)
    df['body']    = (df['close'] - df['open']).abs()
    df['is_bull'] = df['close'] > df['open']
    df['is_bear'] = df['close'] < df['open']
    return df


# ─────────────────────────────────────────────────────────────
# SESSION FILTER (VN time = Server + (7 - GMT))
# ─────────────────────────────────────────────────────────────
def make_session_mask(df: pd.DataFrame, start_vn: int, end_vn: int,
                      server_gmt: int = 3) -> np.ndarray:
    vn_hours = (df['datetime'] + pd.Timedelta(hours=(7 - server_gmt))).dt.hour
    return ((vn_hours >= start_vn) & (vn_hours < end_vn)).values


# ─────────────────────────────────────────────────────────────
# SIGNAL DETECTION (vectorized)
# ─────────────────────────────────────────────────────────────
@dataclass
class SignalConfig:
    sl_pts:             float = 50.0
    tp_pts:             float = 50.0    # equal to SL for 1:1 RR
    be_activate_pts:    float = 25.0    # 50% of SL
    be_lock_pts:        float = 10.0
    ema_buffer_pts:     float = 30.0
    rsi_buy_min:        float = 50.0
    rsi_buy_max:        float = 65.0
    rsi_sell_min:       float = 35.0
    rsi_sell_max:       float = 52.0
    require_ema50_touch: bool = True     # Pullback phải chạm EMA50
    min_body_pts:       float = 8.0     # Tối thiểu body nến (lọc doji)
    session_start_vn:   int   = 15      # 15:00 VN
    session_end_vn:     int   = 21      # 21:00 VN
    risk_pct:           float = 1.0
    max_daily_loss:     float = 3.0
    max_consec_loss:    int   = 2
    max_spread_pts:     float = 15.0
    spread_pts:         float = 6.0     # assumed constant spread
    server_gmt:         int   = 3
    initial_balance:    float = 100.0


def detect_signals(df: pd.DataFrame, cfg: SignalConfig) -> np.ndarray:
    """Vectorized signal detection. Returns array: 0=none, 1=BUY, -1=SELL."""
    n    = len(df)
    buf  = cfg.ema_buffer_pts * POINT
    mbody= cfg.min_body_pts * POINT
    sprd = cfg.spread_pts * POINT

    e21  = df['ema21'].values
    e50  = df['ema50'].values
    e21p = df['ema21_p'].values
    e50p = df['ema50_p'].values
    rsi  = df['rsi'].values
    hi   = df['high'].values
    lo   = df['low'].values
    cl   = df['close'].values
    op   = df['open'].values
    clp  = df['close_p'].values
    body = df['body'].values
    is_bull = df['is_bull'].values
    is_bear = df['is_bear'].values
    sess = make_session_mask(df, cfg.session_start_vn, cfg.session_end_vn, cfg.server_gmt)

    # Pullback reference: EMA50 if require_ema50_touch else EMA21
    pull_ref = e50 if cfg.require_ema50_touch else e21

    # BUY conditions (vectorized)
    buy_trend    = (e21 > e50) & (e21p > e50p)
    buy_pullback = lo <= pull_ref + buf
    buy_bounce   = (cl > e21) & is_bull
    buy_rsi      = (rsi >= cfg.rsi_buy_min) & (rsi <= cfg.rsi_buy_max)
    buy_strength = clp > e50p
    buy_body     = body >= mbody if mbody > 0 else np.ones(n, dtype=bool)
    buy_session  = sess
    buy_spread   = sprd / POINT <= cfg.max_spread_pts

    # SELL conditions (vectorized)
    sell_trend    = (e21 < e50) & (e21p < e50p)
    sell_pullback = hi >= pull_ref - buf
    sell_bounce   = (cl < e21) & is_bear
    sell_rsi      = (rsi >= cfg.rsi_sell_min) & (rsi <= cfg.rsi_sell_max)
    sell_strength = clp < e50p
    sell_body     = body >= mbody if mbody > 0 else np.ones(n, dtype=bool)
    sell_session  = sess

    signals = np.zeros(n, dtype=np.int8)
    buy_all  = buy_trend & buy_pullback & buy_bounce & buy_rsi & buy_strength & buy_body & buy_session
    sell_all = sell_trend & sell_pullback & sell_bounce & sell_rsi & sell_strength & sell_body & sell_session

    signals[buy_all]  = 1
    signals[sell_all] = -1
    return signals


# ─────────────────────────────────────────────────────────────
# TRADE SIMULATION (bar-by-bar)
# ─────────────────────────────────────────────────────────────
@dataclass
class Trade:
    entry_time:  datetime
    direction:   int        # 1=BUY, -1=SELL
    lots:        float
    entry:       float
    sl:          float
    tp:          float
    exit_time:   Optional[datetime] = None
    exit_price:  float = 0.0
    pnl:         float = 0.0
    reason:      str   = ''
    be_moved:    bool  = False


def calc_lot(balance: float, sl_pts: float, risk_pct: float) -> float:
    risk_usd = balance * risk_pct / 100.0
    lot = risk_usd / (sl_pts * 10.0)
    lot = max(0.01, round(lot / 0.01) * 0.01)
    return min(lot, 200.0)


def simulate_trades(df: pd.DataFrame, signals: np.ndarray,
                    cfg: SignalConfig) -> List[Trade]:
    """Bar-by-bar simulation. Signal on bar[i] → enter on bar[i+1] open."""
    times  = df['datetime'].values
    opens  = df['open'].values
    highs  = df['high'].values
    lows   = df['low'].values
    closes = df['close'].values

    sl_pts  = cfg.sl_pts  * POINT
    tp_pts  = cfg.tp_pts  * POINT
    be_act  = cfg.be_activate_pts * POINT
    be_lock = cfg.be_lock_pts * POINT
    sprd    = cfg.spread_pts * POINT

    vn_offset = pd.Timedelta(hours=(7 - cfg.server_gmt))
    sess = make_session_mask(df, cfg.session_start_vn, cfg.session_end_vn, cfg.server_gmt)

    trades: List[Trade] = []
    balance  = cfg.initial_balance
    in_trade = False
    trade: Optional[Trade] = None

    day_loss    = 0.0
    day_start_b = balance
    day_date    = None
    consec_loss = 0

    n = len(df)
    i = 0
    while i < n:
        ts_i = pd.Timestamp(times[i])
        vn_dt = ts_i + vn_offset
        cur_date = vn_dt.date()

        # Daily reset
        if cur_date != day_date:
            day_date    = cur_date
            day_loss    = 0.0
            day_start_b = balance
            consec_loss = 0

        if in_trade and trade is not None:
            # ── manage open trade ──────────────────────────────
            d   = trade.direction
            cur_sl = trade.sl
            cur_tp = trade.tp

            # BE logic: when profit ≥ be_activate, move SL to entry + be_lock
            if not trade.be_moved:
                if d == 1 and lows[i] > trade.entry:   # rough profit check
                    profit_pts = lows[i] - trade.entry
                    if profit_pts >= be_act:
                        new_sl = trade.entry + be_lock
                        if new_sl > cur_sl:
                            trade.sl = new_sl
                            trade.be_moved = True
                elif d == -1 and highs[i] < trade.entry:
                    profit_pts = trade.entry - highs[i]
                    if profit_pts >= be_act:
                        new_sl = trade.entry - be_lock
                        if new_sl < cur_sl:
                            trade.sl = new_sl
                            trade.be_moved = True

            # Check SL / TP hit (bar OHLC)
            hit_sl = hit_tp = False
            exit_px = 0.0
            reason = ''

            if d == 1:
                if lows[i] <= trade.sl:
                    hit_sl = True; exit_px = trade.sl; reason = 'SL'
                elif highs[i] >= trade.tp:
                    hit_tp = True; exit_px = trade.tp; reason = 'TP'
            else:
                if highs[i] >= trade.sl:
                    hit_sl = True; exit_px = trade.sl; reason = 'SL'
                elif lows[i] <= trade.tp:
                    hit_tp = True; exit_px = trade.tp; reason = 'TP'

            # EOD: close at end of session or market close
            is_eod = False
            if not hit_sl and not hit_tp:
                vn_h = vn_dt.hour
                # Close at last bar of session or if next bar is out-of-session
                if vn_h >= cfg.session_end_vn - 1:
                    next_in = sess[i + 1] if i + 1 < n else False
                    if not next_in:
                        is_eod   = True
                        exit_px  = closes[i]
                        reason   = 'EOD'
                # Friday close (avoid weekend gap)
                if vn_dt.weekday() == 4 and vn_h >= 21:
                    is_eod  = True
                    exit_px = closes[i]
                    reason  = 'EOD'

            if hit_sl or hit_tp or is_eod:
                pnl = (exit_px - trade.entry) * d * trade.lots * CONTRACT
                trade.exit_time  = ts_i
                trade.exit_price = exit_px
                trade.pnl        = round(pnl, 2)
                trade.reason     = reason
                trades.append(trade)
                balance += pnl
                if pnl <= 0:
                    day_loss    += abs(pnl)
                    consec_loss += 1
                else:
                    consec_loss = 0
                in_trade = False
                trade    = None
            i += 1
            continue

        # ── check for new entry signal ─────────────────────────
        # Signal on bar[i], enter on bar[i+1]
        if (signals[i] != 0 and not in_trade
                and i + 1 < n and sess[i]):

            # Daily loss / consec loss guard
            daily_dd = day_loss / day_start_b * 100.0 if day_start_b > 0 else 0.0
            if daily_dd >= cfg.max_daily_loss:
                i += 1; continue
            if consec_loss >= cfg.max_consec_loss:
                i += 1; continue

            sig   = int(signals[i])
            entry = opens[i + 1] + (sprd if sig == 1 else 0.0)
            sl    = entry - sl_pts * sig
            tp    = entry + tp_pts * sig
            lots  = calc_lot(balance, cfg.sl_pts, cfg.risk_pct)

            trade = Trade(
                entry_time=pd.Timestamp(times[i + 1]),
                direction=sig, lots=lots,
                entry=entry, sl=sl, tp=tp
            )
            in_trade = True
            i += 1  # skip entry bar, manage from i+1
            continue

        i += 1

    return trades


# ─────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────
def compute_stats(trades: List[Trade], initial_balance: float) -> dict:
    if not trades:
        return dict(n=0, wins=0, wr=0.0, pnl=0.0, dd=0.0, pf=0.0)
    n     = len(trades)
    wins  = sum(1 for t in trades if t.pnl > 0)
    pnl   = sum(t.pnl for t in trades)
    gross_win  = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    pf    = gross_win / gross_loss if gross_loss > 0 else float('inf')

    # Max drawdown
    bal = initial_balance
    peak = bal
    max_dd = 0.0
    for t in trades:
        bal += t.pnl
        peak = max(peak, bal)
        dd = (peak - bal) / peak * 100.0
        max_dd = max(max_dd, dd)

    # Close reasons
    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1

    return dict(
        n=n, wins=wins, wr=wins/n*100 if n else 0,
        pnl=pnl, dd=max_dd, pf=pf,
        tp=reasons.get('TP', 0), sl=reasons.get('SL', 0),
        be=reasons.get('BE', 0), eod=reasons.get('EOD', 0),
    )


# ─────────────────────────────────────────────────────────────
# SINGLE BACKTEST RUN
# ─────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, cfg: SignalConfig) -> dict:
    signals = detect_signals(df, cfg)
    trades  = simulate_trades(df, signals, cfg)
    stats   = compute_stats(trades, cfg.initial_balance)
    return stats


# ─────────────────────────────────────────────────────────────
# GRID SEARCH
# ─────────────────────────────────────────────────────────────
GRID = {
    'sl_tp_pts':          [30, 40, 50, 60, 80],
    'rsi_buy_range':      [(48, 65), (50, 65), (50, 70), (55, 70), (45, 62)],
    'rsi_sell_range':     [(30, 52), (33, 55), (30, 48), (35, 52), (30, 55)],
    'ema_buffer_pts':     [15, 25, 35, 45],
    'require_ema50_touch':[True, False],
    'min_body_pts':       [0, 8, 15, 25],
    'session_vn':         [(14, 22), (14, 20), (15, 21), (15, 20), (16, 21)],
}

def make_cfg(params: dict) -> SignalConfig:
    sl  = params['sl_tp_pts']
    rbi = params['rsi_buy_range']
    rsi = params['rsi_sell_range']
    ses = params['session_vn']
    return SignalConfig(
        sl_pts              = sl,
        tp_pts              = sl,           # RR 1:1
        be_activate_pts     = sl * 0.5,     # BE at 50% of SL
        be_lock_pts         = max(5, sl * 0.1),
        ema_buffer_pts      = params['ema_buffer_pts'],
        rsi_buy_min         = rbi[0],
        rsi_buy_max         = rbi[1],
        rsi_sell_min        = rsi[0],
        rsi_sell_max        = rsi[1],
        require_ema50_touch = params['require_ema50_touch'],
        min_body_pts        = params['min_body_pts'],
        session_start_vn    = ses[0],
        session_end_vn      = ses[1],
    )


def grid_search(df: pd.DataFrame, min_trades: int = 20,
                min_wr: float = 60.0) -> List[dict]:
    keys  = list(GRID.keys())
    vals  = [GRID[k] for k in keys]
    total = 1
    for v in vals: total *= len(v)

    print(f"\nGrid search: {total} combinations on {len(df):,} bars...")
    print(f"Filter: WR >= {min_wr}%, trades >= {min_trades}")

    results = []
    t0 = time.time()

    for idx, combo in enumerate(itertools.product(*vals)):
        params = dict(zip(keys, combo))
        cfg    = make_cfg(params)
        stats  = run_backtest(df, cfg)

        if stats['n'] >= min_trades and stats['wr'] >= min_wr:
            row = {**params, **stats}
            row['sl_pts'] = params['sl_tp_pts']
            results.append(row)

        if (idx + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate    = (idx + 1) / elapsed
            eta     = (total - idx - 1) / rate
            print(f"  [{idx+1}/{total}]  {len(results)} candidates  "
                  f"ETA {eta:.0f}s  best WR={max((r['wr'] for r in results), default=0):.1f}%")

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s  →  {len(results)} candidates pass filter\n")
    results.sort(key=lambda r: (-r['wr'], -r['pnl']))
    return results


# ─────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────
def print_results(results: List[dict], top_n: int = 30):
    if not results:
        print("No configurations found meeting criteria.")
        return

    print("=" * 90)
    print(f"  TOP {min(top_n, len(results))} CONFIGURATIONS  (sorted by WR desc, P&L desc)")
    print("=" * 90)

    headers = ['Rank', 'SL/TP', 'RSI_buy', 'RSI_sell', 'EMA_buf',
               'EMA50?', 'Body', 'Session', 'N', 'WR%', 'P&L', 'DD%', 'PF']
    rows = []
    for rank, r in enumerate(results[:top_n], 1):
        ses = r['session_vn']
        rows.append([
            rank,
            f"{r['sl_tp_pts']}pts",
            f"{r['rsi_buy_range'][0]}-{r['rsi_buy_range'][1]}",
            f"{r['rsi_sell_range'][0]}-{r['rsi_sell_range'][1]}",
            f"{r['ema_buffer_pts']}pts",
            'YES' if r['require_ema50_touch'] else 'no',
            f"{r['min_body_pts']}pts",
            f"{ses[0]}-{ses[1]}VN",
            r['n'],
            f"{r['wr']:.1f}%",
            f"${r['pnl']:.2f}",
            f"{r['dd']:.1f}%",
            f"{r['pf']:.2f}",
        ])

    if HAS_TAB:
        print(tabulate(rows, headers=headers, tablefmt='simple'))
    else:
        print(' | '.join(f'{h:<9}' for h in headers))
        print('-' * 100)
        for row in rows:
            print(' | '.join(f'{str(v):<9}' for v in row))

    # Best config detail
    best = results[0]
    print("\n" + "=" * 60)
    print("  BEST CONFIG DETAIL")
    print("=" * 60)
    print(f"  SL = TP          : {best['sl_tp_pts']} pts  (RR 1:1)")
    print(f"  RSI BUY          : {best['rsi_buy_range'][0]} – {best['rsi_buy_range'][1]}")
    print(f"  RSI SELL         : {best['rsi_sell_range'][0]} – {best['rsi_sell_range'][1]}")
    print(f"  EMA buffer       : {best['ema_buffer_pts']} pts")
    print(f"  Require EMA50    : {'YES' if best['require_ema50_touch'] else 'no'}")
    print(f"  Min body         : {best['min_body_pts']} pts")
    ses = best['session_vn']
    print(f"  Session VN       : {ses[0]}:00 – {ses[1]}:00")
    print(f"  ─────────────────────────────────────")
    print(f"  Trades           : {best['n']}")
    print(f"  Win Rate         : {best['wr']:.1f}%")
    print(f"  Net P&L          : ${best['pnl']:.2f}")
    print(f"  Max Drawdown     : {best['dd']:.1f}%")
    print(f"  Profit Factor    : {best['pf']:.2f}")
    tp  = best.get('tp',  0); sl  = best.get('sl',  0)
    eod = best.get('eod', 0)
    print(f"  Exit: TP={tp} SL={sl} EOD={eod}")

    # Top 5 WR>70%
    winhigh = [r for r in results if r['wr'] >= 70.0]
    if winhigh:
        print(f"\n  >>> {len(winhigh)} configs with WR >= 70%  <<<")
        for r in winhigh[:5]:
            ses = r['session_vn']
            print(f"    SL={r['sl_tp_pts']} RSI_B={r['rsi_buy_range']} "
                  f"RSI_S={r['rsi_sell_range']} buf={r['ema_buffer_pts']} "
                  f"EMA50={'Y' if r['require_ema50_touch'] else 'n'} "
                  f"body={r['min_body_pts']} sess={ses[0]}-{ses[1]}VN "
                  f"| N={r['n']} WR={r['wr']:.1f}% P&L=${r['pnl']:.2f}")
    else:
        # Show configs closest to 70%
        close = [r for r in results if r['wr'] >= 65.0]
        print(f"\n  No WR>=70% found. Best {len(close)} configs with WR>=65%:")
        for r in close[:5]:
            ses = r['session_vn']
            print(f"    SL={r['sl_tp_pts']} RSI_B={r['rsi_buy_range']} "
                  f"buf={r['ema_buffer_pts']} EMA50={'Y' if r['require_ema50_touch'] else 'n'} "
                  f"| N={r['n']} WR={r['wr']:.1f}% P&L=${r['pnl']:.2f}")


# ─────────────────────────────────────────────────────────────
# BASELINE COMPARISON (original EA parameters)
# ─────────────────────────────────────────────────────────────
def run_baseline(df: pd.DataFrame):
    print("\n" + "=" * 60)
    print("  BASELINE: Original EA params (SL=50 TP=100, RR 1:2)")
    print("=" * 60)
    cfg = SignalConfig(
        sl_pts=50, tp_pts=100,
        be_activate_pts=60, be_lock_pts=30,
        ema_buffer_pts=30,
        rsi_buy_min=45, rsi_buy_max=70,
        rsi_sell_min=30, rsi_sell_max=55,
        require_ema50_touch=False,
        min_body_pts=0,
        session_start_vn=14, session_end_vn=22,
    )
    stats = run_backtest(df, cfg)
    print(f"  Trades: {stats['n']}  WR: {stats['wr']:.1f}%  P&L: ${stats['pnl']:.2f}  DD: {stats['dd']:.1f}%")
    return stats


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='ScalpingEURUSD Parameter Optimizer')
    ap.add_argument('--data',   default='eurusd_m5.csv', help='OHLCV CSV file')
    ap.add_argument('--start',  default='2025-04-09',    help='Backtest start date')
    ap.add_argument('--end',    default='2026-04-09',    help='Backtest end date')
    ap.add_argument('--warmup', default=3000, type=int,  help='Warmup bars for EMA (default 3000)')
    ap.add_argument('--min-wr', default=60.0, type=float, help='Minimum WR to report')
    ap.add_argument('--min-n',  default=20,   type=int,   help='Minimum trades to report')
    ap.add_argument('--top',    default=30,   type=int,   help='Show top N results')
    args = ap.parse_args()

    print(f"Loading {args.data}...")
    df_all = load_data(args.data)
    print(f"  Total bars: {len(df_all):,}  {df_all['datetime'].iloc[0]} → {df_all['datetime'].iloc[-1]}")

    # Compute indicators on FULL dataset (including warmup before start)
    df_all = compute_indicators(df_all)

    # Slice test period (but include warmup bars before start for indicator convergence)
    start_dt = pd.Timestamp(args.start)
    end_dt   = pd.Timestamp(args.end)

    warmup_start = start_dt - pd.Timedelta(bars=args.warmup) if False else None
    mask = (df_all['datetime'] >= start_dt) & (df_all['datetime'] <= end_dt)
    df_test = df_all[mask].reset_index(drop=True)
    print(f"  Test period: {df_test['datetime'].iloc[0]} → {df_test['datetime'].iloc[-1]}  ({len(df_test):,} bars)")

    # Baseline
    run_baseline(df_test)

    # Grid search
    results = grid_search(df_test, min_trades=args.min_n, min_wr=args.min_wr)
    print_results(results, top_n=args.top)

    # Save results
    if results:
        out = []
        for r in results:
            ses = r['session_vn']
            out.append({
                'sl_tp_pts': r['sl_tp_pts'],
                'rsi_buy_min': r['rsi_buy_range'][0], 'rsi_buy_max': r['rsi_buy_range'][1],
                'rsi_sell_min': r['rsi_sell_range'][0], 'rsi_sell_max': r['rsi_sell_range'][1],
                'ema_buffer_pts': r['ema_buffer_pts'],
                'require_ema50_touch': r['require_ema50_touch'],
                'min_body_pts': r['min_body_pts'],
                'session_start_vn': ses[0], 'session_end_vn': ses[1],
                'trades': r['n'], 'wr_pct': round(r['wr'], 1),
                'pnl_usd': round(r['pnl'], 2),
                'max_dd_pct': round(r['dd'], 1),
                'profit_factor': round(r['pf'], 2),
            })
        pd.DataFrame(out).to_csv('optimize_results.csv', index=False)
        print(f"\nResults saved to optimize_results.csv ({len(out)} rows)")


if __name__ == '__main__':
    main()
