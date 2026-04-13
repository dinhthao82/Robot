#!/usr/bin/env python3
"""
ScalpingEURUSD – Backtest vs MT5 Comparator
============================================
Đọc Phase-1 CSV log do EA ghi ra, sau đó:
  1. Reconstruct OHLCV từ BAR_ANALYSIS rows (H/L/O/C trong NOTE field)
  2. Chạy lại signal detection bằng Python (cùng logic MQL5)
  3. Mô phỏng từng lệnh (SL/TP/BE/Trail)
  4. So sánh kết quả Python vs kết quả MT5 logged

Cách chạy:
  python compare_log.py --log1 SEUR_EURUSD_M5_TESTER_20250409_000000.csv
  python compare_log.py --log1 <file1.csv> --log2 <file2.csv>
"""

import argparse
import re
import sys
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("pip install pandas numpy")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────
# CONFIG (phải khớp EA v1.10 defaults)
# ──────────────────────────────────────────────────────────────
@dataclass
class Config:
    session_start:   int   = 14
    session_end:     int   = 22
    server_gmt:      int   = 3
    force_close_eod: bool  = True
    risk_pct:        float = 1.0
    max_daily_loss:  float = 3.0
    max_consec_loss: int   = 2
    max_spread_pts:  float = 20.0
    sl_pts:          float = 50.0
    tp_pts:          float = 100.0
    use_be:          bool  = True
    be_activate_pts: float = 60.0
    be_lock_pts:     float = 30.0      # v1.10 fix: 5 → 30
    use_trail:       bool  = True
    trail_start_pts: float = 80.0
    trail_step_pts:  float = 30.0
    fast_ema:        int   = 21
    slow_ema:        int   = 50
    ema_buffer_pts:  float = 30.0
    rsi_period:      int   = 14
    rsi_buy_min:     float = 45.0
    rsi_buy_max:     float = 70.0
    rsi_sell_min:    float = 30.0
    rsi_sell_max:    float = 55.0
    initial_balance: float = 100.0
    commission_usd:  float = 0.0
    point:           float = 0.00001
    contract_size:   float = 100_000.0
    lot_min:         float = 0.01
    lot_max:         float = 200.0
    lot_step:        float = 0.01
    default_spread:  float = 6.0


# ──────────────────────────────────────────────────────────────
# PARSE INIT ROW → override Config từ EA log
# ──────────────────────────────────────────────────────────────
def parse_config_from_init(note: str) -> dict:
    """Đọc NOTE của INIT row để lấy config thực tế của EA khi test."""
    mapping = {
        'SessStart': ('session_start', int),
        'SessEnd':   ('session_end',   int),
        'ServerGMT': ('server_gmt',    int),
        'Risk':      ('risk_pct',      float),
        'MaxDailyLoss': ('max_daily_loss', float),
        'MaxConsecLoss': ('max_consec_loss', int),
        'MaxSpread': ('max_spread_pts', float),
        'SL':        ('sl_pts',        float),
        'TP':        ('tp_pts',        float),
        'BEAct':     ('be_activate_pts', float),
        'BELck':     ('be_lock_pts',   float),
        'TrlStart':  ('trail_start_pts', float),
        'TrlStep':   ('trail_step_pts', float),
        'FastEMA':   ('fast_ema',      int),
        'SlowEMA':   ('slow_ema',      int),
        'EMABuf':    ('ema_buffer_pts', float),
        'RSI':       ('rsi_period',    int),
    }
    result = {}
    for key, (field, typ) in mapping.items():
        m = re.search(rf'{key}=([0-9.]+)', note)
        if m:
            result[field] = typ(m.group(1))
    return result


# ──────────────────────────────────────────────────────────────
# PARSE PHASE-1 CSV
# ──────────────────────────────────────────────────────────────
@dataclass
class LogBar:
    server_time: datetime
    bar_time:    datetime
    open:   float; high:  float; low:   float; close: float
    ema21:  float; ema50: float; rsi:   float
    spread: float
    logged_signal: str      # NONE / BUY / SELL
    logged_conditions: dict

@dataclass
class LogTrade:
    open_time:   datetime
    bar_time:    datetime
    direction:   int
    lots:        float
    open_price:  float
    sl:          float
    tp:          float
    ema21:       float; ema50: float; rsi: float; spread: float
    # Set khi có CLOSE row
    close_time:  Optional[datetime] = None
    close_price: float = 0.0
    net_profit:  float = 0.0
    reason:      str   = ""
    modifies:    List[float] = field(default_factory=list)   # new_sl values


def parse_phase1_csv(filepath: str) -> Tuple[List[LogBar], List[LogTrade], Config]:
    """Parse toàn bộ Phase-1 CSV log."""
    bars:   List[LogBar]   = []
    trades: List[LogTrade] = []
    cfg = Config()

    open_trades: Dict[str, LogTrade] = {}   # key = ticket

    with open(filepath, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    header = None
    for raw in lines:
        raw = raw.rstrip('\n\r')
        if not raw:
            continue
        parts = raw.split(';')
        if parts[0].strip() == 'EVENT':
            header = parts
            continue

        if len(parts) < 26:
            parts += [''] * (26 - len(parts))

        event       = parts[0].strip()
        server_time = parts[1].strip()
        bar_time    = parts[2].strip()
        ticket      = parts[4].strip()
        direction   = parts[5].strip()
        lots_s      = parts[6].strip()
        open_price_s = parts[7].strip()
        sl_s        = parts[8].strip()
        tp_s        = parts[9].strip()
        new_sl_s    = parts[10].strip()
        close_px_s  = parts[11].strip()
        ema21_s     = parts[12].strip()
        ema50_s     = parts[13].strip()
        rsi_s       = parts[14].strip()
        spread_s    = parts[15].strip()
        profit_s    = parts[16].strip()
        net_s       = parts[19].strip()
        duration_s  = parts[23].strip()
        reason      = parts[24].strip()
        note        = parts[25].strip() if len(parts) > 25 else ''

        def dt(s):
            try:
                return datetime.strptime(s, '%Y.%m.%d %H:%M:%S')
            except:
                try:
                    return datetime.strptime(s, '%Y.%m.%d %H:%M')
                except:
                    return None

        def flt(s, default=0.0):
            try: return float(s)
            except: return default

        if event == 'INIT':
            overrides = parse_config_from_init(note)
            for k, v in overrides.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)

        elif event == 'BAR_ANALYSIS':
            # Extract OHLC từ NOTE: "sig=XX H=... L=... O=... C=..."
            m_h = re.search(r'H=([\d.]+)', note)
            m_l = re.search(r'L=([\d.]+)', note)
            m_o = re.search(r'O=([\d.]+)', note)
            m_c = re.search(r'C=([\d.]+)', note)
            if not all([m_h, m_l, m_o, m_c]):
                continue

            # Parse conditions
            conds = {}
            for key in ['buy_trend', 'buy_pullback', 'buy_bounce', 'buy_rsi', 'buy_strength',
                        'sell_trend', 'sell_pullback', 'sell_bounce', 'sell_rsi', 'sell_strength']:
                short = key.replace('buy_', '').replace('sell_', '')
                prefix = 'BUY' if key.startswith('buy') else 'SELL'
                m = re.search(rf'{prefix}\[.*?{short[0:4]}=(\d)', note, re.IGNORECASE)
                # More precise pattern
                if prefix == 'BUY':
                    m = re.search(r'BUY\[trend=(\d) pull=(\d) bounce=(\d) rsi=(\d) str=(\d)\]', note)
                    if m and key.startswith('buy'):
                        conds = {
                            'buy_trend': bool(int(m.group(1))),
                            'buy_pullback': bool(int(m.group(2))),
                            'buy_bounce': bool(int(m.group(3))),
                            'buy_rsi': bool(int(m.group(4))),
                            'buy_strength': bool(int(m.group(5)))
                        }
                else:
                    m = re.search(r'SELL\[trend=(\d) pull=(\d) bounce=(\d) rsi=(\d) str=(\d)\]', note)
                    if m:
                        conds.update({
                            'sell_trend': bool(int(m.group(1))),
                            'sell_pullback': bool(int(m.group(2))),
                            'sell_bounce': bool(int(m.group(3))),
                            'sell_rsi': bool(int(m.group(4))),
                            'sell_strength': bool(int(m.group(5)))
                        })
                break

            b_dt = dt(bar_time)
            s_dt = dt(server_time)
            if b_dt is None or s_dt is None:
                continue

            bar = LogBar(
                server_time    = s_dt,
                bar_time       = b_dt,
                open   = float(m_o.group(1)), high  = float(m_h.group(1)),
                low    = float(m_l.group(1)), close = float(m_c.group(1)),
                ema21  = flt(ema21_s), ema50 = flt(ema50_s), rsi = flt(rsi_s),
                spread = flt(spread_s, cfg.default_spread),
                logged_signal = direction.strip() if direction else 'NONE',
                logged_conditions = conds,
            )
            bars.append(bar)

        elif event == 'OPEN':
            s_dt = dt(server_time)
            b_dt = dt(bar_time) if bar_time else s_dt
            if s_dt is None: continue
            trade = LogTrade(
                open_time  = s_dt,
                bar_time   = b_dt or s_dt,
                direction  = 1 if direction == 'BUY' else -1,
                lots       = flt(lots_s, 0.01),
                open_price = flt(open_price_s),
                sl         = flt(sl_s),
                tp         = flt(tp_s),
                ema21      = flt(ema21_s), ema50 = flt(ema50_s),
                rsi        = flt(rsi_s), spread = flt(spread_s, cfg.default_spread),
            )
            open_trades[ticket] = trade
            trades.append(trade)

        elif event in ('MODIFY_BE', 'MODIFY_TRAIL'):
            if ticket in open_trades:
                open_trades[ticket].modifies.append(flt(new_sl_s))

        elif event == 'CLOSE':
            c_dt        = dt(server_time)
            open_time_s = parts[22].strip() if len(parts) > 22 else ''
            c_px        = flt(close_px_s)
            c_net       = flt(net_s)

            matched = None

            # Strategy 1: match bằng position ticket (chính xác nhất khi ticket đúng)
            matched = open_trades.pop(ticket, None)

            # Strategy 2: match bằng close_price ≈ SL hoặc TP của open trade
            # ĐỂ TRƯỚC OPEN_TIME vì g_openTime bị contaminate khi OPEN+CLOSE cùng tick:
            #   MT5 tester gọi OnTick (→ OPEN logged) TRƯỚC OnTradeTransaction (→ CLOSE logged)
            #   nên g_openTime bị ghi đè bởi lệnh mới, nhưng close_price vật lý vẫn chính xác
            if matched is None and c_px > 0:
                tol = cfg.point * 3   # 3 points tolerance
                best_key = None
                for key, t in list(open_trades.items()):
                    if abs(c_px - t.sl) <= tol or abs(c_px - t.tp) <= tol:
                        best_key = key
                        break
                if best_key is None:
                    # Strategy 2b: net P&L match – tính exp_net dùng close_price × từng open trade
                    for key, t in list(open_trades.items()):
                        exp_net = round((c_px - t.open_price) * t.direction * t.lots * 100_000, 2)
                        if abs(exp_net - c_net) <= 0.02:
                            best_key = key
                            break
                if best_key is not None:
                    matched = open_trades.pop(best_key, None)

            # Strategy 3: match bằng OPEN_TIME column (sau cùng vì có thể bị contaminate)
            if matched is None and open_time_s:
                open_dt_ref = dt(open_time_s)
                if open_dt_ref:
                    for key, t in list(open_trades.items()):
                        if abs((t.open_time - open_dt_ref).total_seconds()) < 2:
                            matched = open_trades.pop(key, None)
                            break

            # Strategy 4: FIFO fallback
            if matched is None:
                for t in trades:
                    if t.close_time is None:
                        key_to_pop = next((k for k, v in open_trades.items() if v is t), None)
                        if key_to_pop:
                            open_trades.pop(key_to_pop)
                        matched = t
                        break

            if matched is not None:
                matched.close_time  = c_dt
                matched.close_price = c_px
                matched.net_profit  = c_net
                matched.reason      = reason

    return bars, trades, cfg


# ──────────────────────────────────────────────────────────────
# RECONSTRUCT OHLCV DataFrame từ BAR_ANALYSIS rows
# ──────────────────────────────────────────────────────────────
def bars_to_df(bars: List[LogBar]) -> pd.DataFrame:
    """Tạo DataFrame OHLCV từ LogBar list, sorted by bar_time."""
    rows = [{
        'datetime': b.bar_time,
        'open':  b.open,  'high': b.high,
        'low':   b.low,   'close': b.close,
        'spread': b.spread,
        'ema21_logged': b.ema21,
        'ema50_logged': b.ema50,
        'rsi_logged':   b.rsi,
        'logged_signal': b.logged_signal,
    } for b in bars]

    df = pd.DataFrame(rows).drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)
    return df


# ──────────────────────────────────────────────────────────────
# COMPUTE INDICATORS (matching MT5)
# ──────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    c = df['close']
    df['ema21'] = c.ewm(span=cfg.fast_ema, adjust=False).mean()
    df['ema50'] = c.ewm(span=cfg.slow_ema, adjust=False).mean()
    delta    = c.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / cfg.rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / cfg.rsi_period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    df['rsi']  = (100 - 100 / (1 + rs)).fillna(50.0)
    return df


# ──────────────────────────────────────────────────────────────
# SESSION FILTER
# ──────────────────────────────────────────────────────────────
def is_session(dt_: datetime, cfg: Config) -> bool:
    vn = dt_ + timedelta(hours=(7 - cfg.server_gmt))
    return cfg.session_start <= vn.hour < cfg.session_end


# ──────────────────────────────────────────────────────────────
# SIGNAL DETECTION (Python translation của MQL5 GetSignal)
# ──────────────────────────────────────────────────────────────
def get_signal(df: pd.DataFrame, i: int, cfg: Config) -> Tuple[str, dict]:
    if i < 2:
        return 'NONE', {}
    buf = cfg.ema_buffer_pts * cfg.point
    ema21_i = df['ema21'].iloc[i]; ema50_i = df['ema50'].iloc[i]; rsi_i = df['rsi'].iloc[i]
    high_i  = df['high'].iloc[i];  low_i   = df['low'].iloc[i]
    close_i = df['close'].iloc[i]; open_i  = df['open'].iloc[i]
    ema21_p = df['ema21'].iloc[i-1]; ema50_p = df['ema50'].iloc[i-1]
    close_p = df['close'].iloc[i-1]

    c = {
        'buy_trend':    (ema21_i > ema50_i) and (ema21_p > ema50_p),
        'buy_pullback': (low_i <= ema21_i + buf),
        'buy_bounce':   (close_i > ema21_i) and (close_i > open_i),
        'buy_rsi':      (cfg.rsi_buy_min <= rsi_i <= cfg.rsi_buy_max),
        'buy_strength': (close_p > ema50_p),
        'sell_trend':   (ema21_i < ema50_i) and (ema21_p < ema50_p),
        'sell_pullback':(high_i >= ema21_i - buf),
        'sell_bounce':  (close_i < ema21_i) and (close_i < open_i),
        'sell_rsi':     (cfg.rsi_sell_min <= rsi_i <= cfg.rsi_sell_max),
        'sell_strength':(close_p < ema50_p),
    }
    if all(c[k] for k in ['buy_trend','buy_pullback','buy_bounce','buy_rsi','buy_strength']):
        return 'BUY', c
    if all(c[k] for k in ['sell_trend','sell_pullback','sell_bounce','sell_rsi','sell_strength']):
        return 'SELL', c
    return 'NONE', c


# ──────────────────────────────────────────────────────────────
# P&L FORMULA (EURUSD standard)
# ──────────────────────────────────────────────────────────────
def calc_pnl(direction: int, lots: float, open_px: float, close_px: float) -> float:
    return round((close_px - open_px) * direction * lots * 100_000, 2)


# ──────────────────────────────────────────────────────────────
# COMPARE P&L: logged vs computed
# ──────────────────────────────────────────────────────────────
def compare_trades(trades: List[LogTrade]) -> List[dict]:
    results = []
    for i, t in enumerate(trades):
        if t.close_time is None:
            results.append({'id': i+1, 'status': 'OPEN_AT_END', 'match': 'N/A'})
            continue

        computed = calc_pnl(t.direction, t.lots, t.open_price, t.close_price)
        diff = round(computed - t.net_profit, 4)
        match = 'OK' if abs(diff) <= 0.02 else 'DIFF'

        dir_str = 'BUY' if t.direction == 1 else 'SELL'
        results.append({
            'id':          i + 1,
            'time':        t.open_time.strftime('%m-%d %H:%M'),
            'dir':         dir_str,
            'lots':        t.lots,
            'entry':       f'{t.open_price:.5f}',
            'exit':        f'{t.close_price:.5f}',
            'reason':      t.reason,
            'logged_net':  t.net_profit,
            'computed':    computed,
            'diff':        diff,
            'match':       match,
            'modifies':    len(t.modifies),
        })
    return results


# ──────────────────────────────────────────────────────────────
# COMPARE SIGNALS using LOGGED EMA/RSI values (no warmup needed)
# ──────────────────────────────────────────────────────────────
def get_signal_from_values(
    ema21_i: float, ema50_i: float, rsi_i: float,
    high_i: float, low_i: float, close_i: float, open_i: float,
    ema21_p: float, ema50_p: float, close_p: float,
    cfg: Config
) -> str:
    """
    Chạy lại signal logic dùng giá trị EMA/RSI đã cho (không recompute).
    Đây là Python translation 1:1 của GetSignal() trong MQL5.
    """
    buf = cfg.ema_buffer_pts * cfg.point

    buy_trend    = (ema21_i > ema50_i) and (ema21_p > ema50_p)
    buy_pullback = (low_i <= ema21_i + buf)
    buy_bounce   = (close_i > ema21_i) and (close_i > open_i)
    buy_rsi      = (cfg.rsi_buy_min <= rsi_i <= cfg.rsi_buy_max)
    buy_strength = (close_p > ema50_p)

    if buy_trend and buy_pullback and buy_bounce and buy_rsi and buy_strength:
        return 'BUY'

    sell_trend    = (ema21_i < ema50_i) and (ema21_p < ema50_p)
    sell_pullback = (high_i >= ema21_i - buf)
    sell_bounce   = (close_i < ema21_i) and (close_i < open_i)
    sell_rsi      = (cfg.rsi_sell_min <= rsi_i <= cfg.rsi_sell_max)
    sell_strength = (close_p < ema50_p)

    if sell_trend and sell_pullback and sell_bounce and sell_rsi and sell_strength:
        return 'SELL'

    return 'NONE'


# ──────────────────────────────────────────────────────────────
# COMPARE SIGNALS: logged BAR_ANALYSIS vs Python recomputed
# ──────────────────────────────────────────────────────────────
def compare_signals(df: pd.DataFrame, bars: List[LogBar]) -> dict:
    """
    So sánh signal của Python engine vs logged signal từ MT5.
    Chỉ xét bars có logged_signal != NONE (các bars EA thực sự phát hiện signal).
    """
    # Tạo lookup: bar_time → index trong df
    dt_to_idx = {row['datetime']: idx for idx, row in df.iterrows()}

    total_bars = 0
    signal_bars = 0   # bars có logged_signal = BUY/SELL
    match_count = 0
    mismatches  = []

    for bar in bars:
        idx = dt_to_idx.get(bar.bar_time)
        if idx is None or idx < 2:
            continue

        total_bars += 1
        py_sig, py_conds = get_signal(df, idx, Config())  # dùng cfg mặc định

        logged_sig = bar.logged_signal

        if logged_sig in ('BUY', 'SELL'):
            signal_bars += 1
            if py_sig == logged_sig:
                match_count += 1
            else:
                mismatches.append({
                    'bar_time':   bar.bar_time.strftime('%Y.%m.%d %H:%M'),
                    'logged':     logged_sig,
                    'python':     py_sig,
                    'ema21_log':  f'{bar.ema21:.5f}',
                    'ema21_py':   f'{df["ema21"].iloc[idx]:.5f}',
                    'rsi_log':    f'{bar.rsi:.2f}',
                    'rsi_py':     f'{df["rsi"].iloc[idx]:.2f}',
                })

    return {
        'total_bars':    total_bars,
        'signal_bars':   signal_bars,
        'signal_match':  match_count,
        'mismatches':    mismatches[:20],   # max 20 để report
    }


# ──────────────────────────────────────────────────────────────
# SIMULATE TRADES (dùng bar data để kiểm tra SL/TP/BE/Trail)
# ──────────────────────────────────────────────────────────────
def simulate_trade_on_bars(trade: LogTrade, df: pd.DataFrame, cfg: Config) -> dict:
    """
    Chạy lại logic SL/TP/BE/Trail trên bar data để xem kết quả simulation
    khác log hay không.
    """
    ep  = trade.open_price
    d   = trade.direction
    sl  = trade.sl
    tp  = trade.tp
    cur_sl = sl
    be_applied = False

    be_act  = cfg.be_activate_pts * cfg.point
    be_lck  = cfg.be_lock_pts     * cfg.point
    tr_st   = cfg.trail_start_pts * cfg.point
    tr_step = cfg.trail_step_pts  * cfg.point

    # Tìm bar đầu tiên >= open_time
    mask = df['datetime'] >= trade.open_time
    if not mask.any():
        return {'sim_reason': 'NO_BAR', 'sim_close': 0.0, 'sim_net': 0.0}

    start_idx = df[mask].index[0]

    for j in range(start_idx, len(df)):
        row  = df.iloc[j]
        b_dt = row['datetime']
        b_h  = row['high'];  b_l = row['low']
        b_o  = row['open'];  b_c = row['close']

        # BE
        if cfg.use_be and not be_applied:
            if (d == 1 and b_h >= ep + be_act) or (d == -1 and b_l <= ep - be_act):
                new_sl = (ep + be_lck) if d == 1 else (ep - be_lck)
                cond   = (new_sl > cur_sl) if d == 1 else (cur_sl == sl or new_sl < cur_sl)
                if cond:
                    cur_sl = new_sl; be_applied = True

        # Trailing
        if cfg.use_trail:
            if d == 1 and b_h >= ep + tr_st:
                new_sl = b_h - tr_step
                if new_sl > cur_sl: cur_sl = new_sl
            elif d == -1 and b_l <= ep - tr_st:
                new_sl = b_l + tr_step
                if cur_sl == sl or new_sl < cur_sl: cur_sl = new_sl

        # EOD
        if cfg.force_close_eod and not is_session(b_dt, cfg):
            cp = b_o
            return {'sim_reason': 'EOD', 'sim_close': cp,
                    'sim_net': calc_pnl(d, trade.lots, ep, cp),
                    'sim_be': be_applied, 'sim_sl': cur_sl}

        # TP
        tp_hit = (d == 1 and b_h >= tp) or (d == -1 and b_l <= tp)
        sl_hit = (d == 1 and b_l <= cur_sl) or (d == -1 and b_h >= cur_sl)

        if tp_hit and sl_hit:
            tp_hit = (b_c > b_o) if d == 1 else (b_c < b_o)
            sl_hit = not tp_hit

        if tp_hit:
            return {'sim_reason': 'TP', 'sim_close': tp,
                    'sim_net': calc_pnl(d, trade.lots, ep, tp),
                    'sim_be': be_applied, 'sim_sl': cur_sl}
        if sl_hit:
            r = 'BE' if be_applied else ('TRAIL_SL' if cur_sl != sl else 'SL')
            return {'sim_reason': r, 'sim_close': cur_sl,
                    'sim_net': calc_pnl(d, trade.lots, ep, cur_sl),
                    'sim_be': be_applied, 'sim_sl': cur_sl}

    return {'sim_reason': 'DATA_END', 'sim_close': df.iloc[-1]['close'],
            'sim_net': calc_pnl(d, trade.lots, ep, df.iloc[-1]['close']),
            'sim_be': be_applied, 'sim_sl': cur_sl}


# ──────────────────────────────────────────────────────────────
# MAIN REPORT
# ──────────────────────────────────────────────────────────────
def print_section(title: str):
    print(f"\n{'='*65}")
    print(f"  {title}")
    print('='*65)


def run_comparison(log_files: List[str], be_lock_override: float = None):
    all_bars:   List[LogBar]   = []
    all_trades: List[LogTrade] = []
    cfg = Config()

    for fpath in log_files:
        print(f"Reading: {fpath}")
        bars, trades, detected_cfg = parse_phase1_csv(fpath)
        all_bars.extend(bars)
        all_trades.extend(trades)
        cfg = detected_cfg   # dùng config từ log

    if be_lock_override is not None:
        cfg.be_lock_pts = be_lock_override
        print(f"  Override BE lock: {be_lock_override} pts")

    print(f"\n  Config from log: SL={cfg.sl_pts} TP={cfg.tp_pts} "
          f"BE_act={cfg.be_activate_pts} BE_lock={cfg.be_lock_pts} "
          f"Trail_start={cfg.trail_start_pts} Trail_step={cfg.trail_step_pts}")
    print(f"  Session: {cfg.session_start}:00–{cfg.session_end}:00 VN | ServerGMT={cfg.server_gmt}")

    closed = [t for t in all_trades if t.close_time is not None]
    open_at_end = [t for t in all_trades if t.close_time is None]
    print(f"\n  Trades found: {len(all_trades)} total | {len(closed)} closed | {len(open_at_end)} open-at-end")
    print(f"  BAR_ANALYSIS rows: {len(all_bars)}")

    # ── 1. P&L Verification ───────────────────────────────────
    print_section("1. P&L Formula Verification (Logged vs Computed)")

    cmp_results = compare_trades(all_trades)
    ok_count   = sum(1 for r in cmp_results if r.get('match') == 'OK')
    diff_count = sum(1 for r in cmp_results if r.get('match') == 'DIFF')

    # Table header
    hdr = f"{'#':>3} {'Time':<13} {'Dir':<5} {'Lots':>5} {'Entry':>10} {'Exit':>10} {'Reason':<10} {'Logged':>8} {'Computed':>9} {'Diff':>7} {'Match'}"
    print(hdr)
    print('-' * len(hdr))

    wins_logged = losses_logged = 0
    for r in cmp_results:
        if r.get('status') == 'OPEN_AT_END':
            print(f"  #{r['id']:>2} [still OPEN at end of data]")
            continue
        flag = 'OK' if r['match'] == 'OK' else 'DIFF'
        print(f"{r['id']:>3} {r['time']:<13} {r['dir']:<5} {r['lots']:>5.2f} "
              f"{r['entry']:>10} {r['exit']:>10} {r['reason']:<10} "
              f"{r['logged_net']:>8.2f} {r['computed']:>9.2f} {r['diff']:>7.4f}  {flag}")
        if r['logged_net'] >= 0: wins_logged += 1
        else: losses_logged += 1

    total_pnl = sum(t.net_profit for t in closed)
    total_computed = sum(calc_pnl(t.direction, t.lots, t.open_price, t.close_price)
                         for t in closed)

    print(f"\n  Match: {ok_count}/{len(cmp_results)} trades  |  "
          f"Diff count: {diff_count}")
    print(f"  Total logged PnL: ${total_pnl:.2f}  |  Computed: ${total_computed:.2f}")
    wr = wins_logged / (wins_logged + losses_logged) * 100 if (wins_logged + losses_logged) > 0 else 0
    print(f"  Win/Loss: {wins_logged}/{losses_logged}  WR={wr:.1f}%")

    # ── 2a. Signal Verification using LOGGED EMA/RSI (accurate) ─
    print_section("2a. Signal Logic Verify (using MT5 logged EMA/RSI values)")

    # Sort bars by bar_time để có bar i-1
    sorted_bars = sorted(all_bars, key=lambda b: b.bar_time)
    sig_ok_logged = sig_diff_logged = 0
    sig_mismatch_logged = []
    for idx in range(1, len(sorted_bars)):
        bar   = sorted_bars[idx]
        bar_p = sorted_bars[idx - 1]    # previous bar

        py_sig = get_signal_from_values(
            ema21_i  = bar.ema21,   ema50_i  = bar.ema50,   rsi_i  = bar.rsi,
            high_i   = bar.high,    low_i    = bar.low,
            close_i  = bar.close,   open_i   = bar.open,
            ema21_p  = bar_p.ema21, ema50_p  = bar_p.ema50,
            close_p  = bar_p.close,
            cfg      = cfg,
        )

        if bar.logged_signal in ('BUY', 'SELL'):
            if py_sig == bar.logged_signal:
                sig_ok_logged += 1
            else:
                sig_diff_logged += 1
                sig_mismatch_logged.append({
                    'bar': bar.bar_time.strftime('%m-%d %H:%M'),
                    'mt5': bar.logged_signal, 'py': py_sig,
                    'ema21': bar.ema21, 'ema50': bar.ema50, 'rsi': bar.rsi,
                    'H': bar.high, 'L': bar.low, 'O': bar.open, 'C': bar.close,
                    'ema21_p': bar_p.ema21, 'ema50_p': bar_p.ema50, 'c_p': bar_p.close,
                })
        elif py_sig in ('BUY', 'SELL'):
            # Python thấy signal nhưng MT5 không thấy
            sig_diff_logged += 1

    total_sig_bars = sig_ok_logged + sig_diff_logged
    pct = sig_ok_logged / total_sig_bars * 100 if total_sig_bars > 0 else 0
    print(f"  Signal bars compared: {total_sig_bars}")
    print(f"  Match: {sig_ok_logged}/{total_sig_bars} ({pct:.1f}%)")
    if sig_mismatch_logged:
        print(f"  Mismatches: {len(sig_mismatch_logged)} — showing first 5:")
        for m in sig_mismatch_logged[:5]:
            buf  = cfg.ema_buffer_pts * cfg.point
            low_cond  = f"low={m['L']:.5f} <= ema21+buf={m['ema21']+buf:.5f} = {m['L'] <= m['ema21']+buf}"
            high_cond = f"high={m['H']:.5f} >= ema21-buf={m['ema21']-buf:.5f} = {m['H'] >= m['ema21']-buf}"
            print(f"    {m['bar']} MT5={m['mt5']} Py={m['py']}")
            print(f"      ema21={m['ema21']:.5f} ema50={m['ema50']:.5f} rsi={m['rsi']:.1f}")
            print(f"      O={m['O']:.5f} H={m['H']:.5f} L={m['L']:.5f} C={m['C']:.5f}")
            print(f"      {low_cond}")
            print(f"      {high_cond}")

    # ── 2. Reconstruct OHLCV + Signal Comparison ─────────────
    print_section("2b. Signal Detection (Python recomputed EMA/RSI vs MT5)")

    if not all_bars:
        print("  No BAR_ANALYSIS rows found.")
    else:
        df = bars_to_df(all_bars)
        df = compute_indicators(df, cfg)

        sig_result = compare_signals(df, all_bars)

        print(f"  Bars analyzed: {sig_result['total_bars']}")
        print(f"  Signal bars (MT5 logged BUY/SELL): {sig_result['signal_bars']}")
        if sig_result['signal_bars'] > 0:
            match_pct = sig_result['signal_match'] / sig_result['signal_bars'] * 100
            print(f"  Python matches: {sig_result['signal_match']}/{sig_result['signal_bars']} ({match_pct:.1f}%)")
            print(f"  Note: low match expected – Python has no warmup history, see section 2a")
        else:
            print("  No signal bars to compare.")

        if sig_result['mismatches']:
            print(f"\n  Mismatches (first {len(sig_result['mismatches'])}):")
            for m in sig_result['mismatches']:
                print(f"    {m['bar_time']}  MT5={m['logged']}  Py={m['python']}  "
                      f"EMA21 log={m['ema21_log']} py={m['ema21_py']}  "
                      f"RSI log={m['rsi_log']} py={m['rsi_py']}")

        # EMA/RSI delta stats
        ema_diffs = []
        rsi_diffs = []
        for bar in all_bars:
            idx_mask = df['datetime'] == bar.bar_time
            if idx_mask.any():
                idx = df[idx_mask].index[0]
                ema_diffs.append(abs(df['ema21'].iloc[idx] - bar.ema21))
                rsi_diffs.append(abs(df['rsi'].iloc[idx] - bar.rsi))

        if ema_diffs:
            print(f"\n  EMA21 delta vs MT5: mean={np.mean(ema_diffs):.6f}  max={np.max(ema_diffs):.6f}")
            print(f"  RSI   delta vs MT5: mean={np.mean(rsi_diffs):.4f}  max={np.max(rsi_diffs):.4f}")

    # ── 3. Trade-level Simulation Comparison ──────────────────
    print_section("3. Trade Simulation: Python BE/Trail/SL-TP vs MT5")

    if all_bars:
        sim_ok = sim_diff = 0
        df_sim = bars_to_df(all_bars)   # reuse (without recomputing)

        for t in closed:
            sim = simulate_trade_on_bars(t, df_sim, cfg)
            net_sim  = round(sim['sim_net'], 2)
            net_log  = t.net_profit
            reason_match = (sim['sim_reason'].split('_')[0] == t.reason or
                            (sim['sim_reason'] == 'BE' and t.reason == 'SL') or
                            (sim['sim_reason'] == 'TRAIL_SL' and t.reason == 'SL'))
            pnl_close = abs(net_sim - net_log) <= 0.10

            if pnl_close:
                sim_ok += 1
            else:
                sim_diff += 1
                dir_s = 'BUY' if t.direction == 1 else 'SELL'
                print(f"  DIFF #{all_trades.index(t)+1} {dir_s} {t.open_time:%m-%d %H:%M} "
                      f"| MT5:{net_log:+.2f}({t.reason}) sim:{net_sim:+.2f}({sim['sim_reason']}) "
                      f"| entry={t.open_price:.5f} exit_log={t.close_price:.5f} "
                      f"sim_close={sim['sim_close']:.5f}")

        note = "(Simulation uses OHLCV from BAR_ANALYSIS only – gaps when position open)"
        print(f"  Simulation match: {sim_ok}/{len(closed)} trades  |  Diff: {sim_diff}")
        print(f"  Note: {note}")
    else:
        print("  No bar data available for simulation.")

    # ── Final Summary ─────────────────────────────────────────
    print_section("SUMMARY")
    wr_str = f"{wr:.1f}%"
    print(f"  Period:        {all_bars[0].bar_time.strftime('%Y.%m.%d') if all_bars else '?'}"
          f" → {all_bars[-1].bar_time.strftime('%Y.%m.%d') if all_bars else '?'}")
    print(f"  Total trades:  {len(closed)} closed")
    print(f"  Win rate:      {wr_str}   W={wins_logged} L={losses_logged}")
    print(f"  Net P&L (log): ${total_pnl:.2f}")
    print(f"  Final balance: ${cfg.initial_balance + total_pnl:.2f}")
    print(f"  P&L formula:   {'MATCH' if abs(total_pnl - total_computed) < 0.05 else 'MISMATCH'}")
    if sig_result['signal_bars'] > 0:
        print(f"  Signal detect: {match_pct:.1f}% match vs MT5")
    print()


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='ScalpingEURUSD – Log vs Engine Comparator')
    parser.add_argument('--log1', required=True,
                        help='Phase-1 CSV log file 1')
    parser.add_argument('--log2', default='',
                        help='Phase-1 CSV log file 2 (optional, cộng vào log1)')
    parser.add_argument('--be-lock', type=float, default=None,
                        help='Override BE lock points (default: đọc từ log)')
    args = parser.parse_args()

    files = [args.log1]
    if args.log2:
        files.append(args.log2)

    run_comparison(files, be_lock_override=args.be_lock)


if __name__ == '__main__':
    main()
