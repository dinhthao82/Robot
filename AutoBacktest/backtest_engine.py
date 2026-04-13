#!/usr/bin/env python3
"""
ScalpingEURUSD – Auto Backtest Engine v1.0
==========================================
Tái tạo kết quả MT5 Strategy Tester từ dữ liệu OHLCV.

Nguồn dữ liệu:
  --mode ohlcv : Chạy standalone từ file OHLCV (export từ MT5)
  --mode csv   : Replay từ Phase-1 log CSV (do EA ghi ra)

Cách export dữ liệu từ MT5:
  1. Mở MT5 → Tools → History Center → EURUSD → M5 → Export
  2. Hoặc dùng script Export để lưu ra file CSV

Chạy:
  python backtest_engine.py --mode ohlcv --data eurusd_m5.csv [--start 2025.04.09] [--end 2025.10.08]
  python backtest_engine.py --mode csv   --log SEUR_EURUSD_M5_TESTER_20250409_000000.csv --data eurusd_m5.csv

Requirements:
  pip install pandas numpy tabulate
"""

import argparse
import os
import sys
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("ERROR: pip install pandas numpy tabulate")
    sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


# =====================================================================
# 1. CONFIG – matching ScalpingEURUSD.mq5 defaults
# =====================================================================
@dataclass
class Config:
    # Session filter (VN time = Server + (7 - ServerGMT))
    session_start:   int   = 14      # VN hour start
    session_end:     int   = 22      # VN hour end (exclusive)
    server_gmt:      int   = 3       # Exness GMT offset
    force_close_eod: bool  = True    # Force close at session end

    # Risk management
    risk_pct:        float = 1.0     # % balance per trade
    max_daily_loss:  float = 3.0     # % daily drawdown limit
    max_consec_loss: int   = 2       # consecutive loss limit
    max_spread_pts:  float = 20.0    # max allowed spread in points

    # SL / TP
    sl_pts:          float = 50.0    # stop loss in points (5 pip)
    tp_pts:          float = 100.0   # take profit in points (10 pip)
    use_be:          bool  = True
    be_activate_pts: float = 60.0    # activate BE when profit >= X pts
    be_lock_pts:     float = 30.0    # lock X pts profit after BE
    use_trail:       bool  = True
    trail_start_pts: float = 80.0    # start trailing when profit >= X pts
    trail_step_pts:  float = 30.0    # trailing step in points

    # EMA
    fast_ema:        int   = 21
    slow_ema:        int   = 50
    ema_buffer_pts:  float = 30.0    # touch zone around EMA21

    # RSI
    rsi_period:      int   = 14
    rsi_buy_min:     float = 45.0
    rsi_buy_max:     float = 70.0
    rsi_sell_min:    float = 30.0
    rsi_sell_max:    float = 55.0

    # Account
    initial_balance: float = 100.0
    commission_usd:  float = 0.0     # per lot per side (Standard = 0)

    # Symbol (EURUSD 5-digit)
    point:           float = 0.00001
    contract_size:   float = 100_000.0
    lot_min:         float = 0.01
    lot_max:         float = 200.0
    lot_step:        float = 0.01
    default_spread:  float = 6.0     # points (used when bar has no spread column)


# =====================================================================
# 2. DATA STRUCTURES
# =====================================================================
@dataclass
class Trade:
    id:           int
    open_time:    datetime
    bar_time:     datetime        # bar that triggered signal (bar[1] time)
    direction:    int             # +1=BUY, -1=SELL
    lots:         float
    open_price:   float           # actual execution price (with spread)
    sl:           float           # original SL
    tp:           float           # original TP
    spread_pts:   float
    ema21:        float           # indicator values at signal bar
    ema50:        float
    rsi:          float

    # Set when closed
    close_time:   Optional[datetime] = None
    close_price:  float = 0.0
    gross_profit: float = 0.0     # raw P&L (no commission)
    commission:   float = 0.0
    net_profit:   float = 0.0
    reason:       str   = ""      # SL / TP / BE / TRAIL / EOD / MAX_HOLD
    duration_min: int   = 0

    # Runtime management (not in output)
    current_sl:   float = 0.0
    be_applied:   bool  = False

    def __post_init__(self):
        self.current_sl = self.sl


@dataclass
class DayStats:
    date:       str
    start_bal:  float
    trades:     int   = 0
    wins:       int   = 0
    losses:     int   = 0
    pnl:        float = 0.0
    stopped:    bool  = False     # consecutive loss limit hit


# =====================================================================
# 3. DATA LOADING
# =====================================================================
def load_mt5_ohlcv(filepath: str) -> pd.DataFrame:
    """
    Load OHLCV từ MT5 export CSV.
    Hỗ trợ hai format:
      Format 1 (tab-separated, cũ): DATE TIME OPEN HIGH LOW CLOSE TICKVOL VOL SPREAD
        Ví dụ: 2025.04.09\t00:00\t1.10105\t1.10120\t1.10095\t1.10112\t150\t0\t6
      Format 2 (comma-separated, mới):
        <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
        2025.04.09,00:00:00,1.10105,1.10120,1.10095,1.10112,150,0,6
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    # Detect format
    with open(filepath, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()

    sep = '\t' if '\t' in first_line else ','

    # Read, skip header if present
    header_row = 0 if any(c.isalpha() for c in first_line.split(sep)[0]) else None

    col_names = ['date', 'time', 'open', 'high', 'low', 'close', 'tickvol', 'vol', 'spread']
    df = pd.read_csv(
        filepath,
        sep=sep,
        names=col_names,
        header=header_row,
        dtype=str
    )

    # Strip angle brackets if present: <DATE> → DATE
    df.columns = [c.strip('<> \t') for c in df.columns]
    df.columns = [c.lower() for c in df.columns]

    # Parse datetime
    # MT5 format: "2025.04.09" and "00:00" or "00:00:00"
    df['datetime'] = pd.to_datetime(
        df['date'].str.replace('.', '-', regex=False) + ' ' + df['time'],
        format='mixed', dayfirst=False
    )

    df['open']   = pd.to_numeric(df['open'],   errors='coerce')
    df['high']   = pd.to_numeric(df['high'],   errors='coerce')
    df['low']    = pd.to_numeric(df['low'],    errors='coerce')
    df['close']  = pd.to_numeric(df['close'],  errors='coerce')
    df['spread'] = pd.to_numeric(df['spread'], errors='coerce').fillna(6.0)

    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    df = df.sort_values('datetime').reset_index(drop=True)
    return df


def load_phase1_csv(filepath: str) -> pd.DataFrame:
    """
    Load Phase-1 log CSV được EA ghi ra.
    Format: EVENT;SERVER_TIME;BAR_TIME;MODE;TICKET;DIR;LOTS;OPEN_PRICE;SL;TP;...
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Phase-1 log not found: {filepath}")
    df = pd.read_csv(filepath, sep=';', dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
    return df


# =====================================================================
# 4. INDICATOR COMPUTATION (matching MT5)
# =====================================================================
def compute_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Tính EMA và RSI khớp với MT5:
    - EMA: ewm(span=N, adjust=False) → α = 2/(N+1), giống MT5 MODE_EMA
    - RSI: Wilder smoothing → ewm(alpha=1/N, adjust=False), giống MT5 iRSI
    """
    close = df['close']

    # EMA (MT5 MODE_EMA = standard EMA with α = 2/(N+1))
    df['ema21'] = close.ewm(span=cfg.fast_ema, adjust=False).mean()
    df['ema50'] = close.ewm(span=cfg.slow_ema, adjust=False).mean()

    # RSI với Wilder smoothing (α = 1/N)
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / cfg.rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / cfg.rsi_period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    df['rsi'] = (100 - 100 / (1 + rs)).fillna(50.0)

    return df


# =====================================================================
# 5. SESSION FILTER
# =====================================================================
def is_session_time(bar_dt: datetime, cfg: Config) -> bool:
    """Server time → VN time → check session."""
    vn_dt = bar_dt + timedelta(hours=(7 - cfg.server_gmt))
    return cfg.session_start <= vn_dt.hour < cfg.session_end


# =====================================================================
# 6. SIGNAL DETECTION (Python translation của GetSignal() trong MQL5)
# =====================================================================
def get_signal(df: pd.DataFrame, i: int, cfg: Config) -> Tuple[int, dict]:
    """
    Kiểm tra tín hiệu tại bar i (bar vừa đóng = bar[1] trong MQL5).
    Trả về: (signal, conditions_dict)
      signal: +1=BUY, -1=SELL, 0=no signal

    Điều kiện BUY (5 conditions):
      1. EMA21 > EMA50 trên bar i và bar i-1 (uptrend ổn định)
      2. Low[i] <= EMA21[i] + buffer (pullback chạm EMA21)
      3. Close[i] > EMA21[i] AND Close[i] > Open[i] (bullish bounce)
      4. RSI[i] trong [BuyMin, BuyMax] (không overbought)
      5. Close[i-1] > EMA50[i-1] (trend mạnh trên bar trước)
    """
    if i < 2:
        return 0, {}

    buf = cfg.ema_buffer_pts * cfg.point

    ema21_i  = df['ema21'].iloc[i]
    ema50_i  = df['ema50'].iloc[i]
    rsi_i    = df['rsi'].iloc[i]
    high_i   = df['high'].iloc[i]
    low_i    = df['low'].iloc[i]
    close_i  = df['close'].iloc[i]
    open_i   = df['open'].iloc[i]

    ema21_p  = df['ema21'].iloc[i - 1]
    ema50_p  = df['ema50'].iloc[i - 1]
    close_p  = df['close'].iloc[i - 1]

    cond = {}

    # --- BUY ---
    cond['buy_trend']    = (ema21_i > ema50_i) and (ema21_p > ema50_p)
    cond['buy_pullback'] = (low_i <= ema21_i + buf)
    cond['buy_bounce']   = (close_i > ema21_i) and (close_i > open_i)
    cond['buy_rsi']      = (cfg.rsi_buy_min <= rsi_i <= cfg.rsi_buy_max)
    cond['buy_strength'] = (close_p > ema50_p)

    if all([cond['buy_trend'], cond['buy_pullback'], cond['buy_bounce'],
            cond['buy_rsi'],   cond['buy_strength']]):
        return 1, cond

    # --- SELL ---
    cond['sell_trend']    = (ema21_i < ema50_i) and (ema21_p < ema50_p)
    cond['sell_pullback'] = (high_i >= ema21_i - buf)
    cond['sell_bounce']   = (close_i < ema21_i) and (close_i < open_i)
    cond['sell_rsi']      = (cfg.rsi_sell_min <= rsi_i <= cfg.rsi_sell_max)
    cond['sell_strength'] = (close_p < ema50_p)

    if all([cond['sell_trend'], cond['sell_pullback'], cond['sell_bounce'],
            cond['sell_rsi'],   cond['sell_strength']]):
        return -1, cond

    return 0, cond


# =====================================================================
# 7. LOT SIZE CALCULATION
# =====================================================================
def calc_lot_size(balance: float, cfg: Config) -> float:
    """
    Tính lot size dựa theo % risk.
    risk_$ = balance × risk_pct/100
    pip_value(1 lot EURUSD) = $10
    lot = risk_$ / (sl_pips × 10)
    """
    risk_usd = balance * cfg.risk_pct / 100.0
    sl_pips  = cfg.sl_pts / 10.0          # 50 pts = 5 pips
    pip_val  = 10.0                         # $10 per pip per lot for EURUSD
    lot = risk_usd / (sl_pips * pip_val)
    lot = math.floor(lot / cfg.lot_step) * cfg.lot_step
    return max(cfg.lot_min, min(cfg.lot_max, round(lot, 2)))


# =====================================================================
# 8. P&L CALCULATION
# =====================================================================
def calc_pnl(direction: int, lots: float, open_price: float,
             close_price: float, cfg: Config) -> float:
    """
    Gross P&L = (close - open) × direction × lots × contract_size
    Ví dụ BUY: lots=0.02, open=1.10328, close=1.10378
      PnL = (1.10378-1.10328) × 1 × 0.02 × 100000 = 0.0005 × 2000 = $1.00
    """
    return (close_price - open_price) * direction * lots * cfg.contract_size


# =====================================================================
# 9. TRADE SIMULATION (bar-by-bar)
# =====================================================================
def simulate_trade(trade: Trade, df: pd.DataFrame, entry_bar_idx: int,
                   cfg: Config) -> Trade:
    """
    Mô phỏng một lệnh từ lúc mở đến khi đóng.
    - entry_bar_idx: index của bar đầu tiên sau khi signal fire (bar hiện tại trong MT5)
    - Xử lý từng bar: kiểm tra BE → trailing → SL/TP → EOD

    MT5 tester dùng real ticks; ở đây ta dùng OHLC approximation:
      - BUY: SL hit khi bar.low  <= current_sl
             TP hit khi bar.high >= trade.tp
             BE/Trail khi bar.high đạt ngưỡng
      - SELL: SL hit khi bar.high >= current_sl
              TP hit khi bar.low  <= trade.tp
              BE/Trail khi bar.low đạt ngưỡng
    """
    be_act  = cfg.be_activate_pts * cfg.point
    be_lck  = cfg.be_lock_pts * cfg.point
    tr_st   = cfg.trail_start_pts * cfg.point
    tr_step = cfg.trail_step_pts * cfg.point
    d       = trade.direction

    for j in range(entry_bar_idx, len(df)):
        bar  = df.iloc[j]
        b_dt = bar['datetime']
        b_o  = bar['open']
        b_h  = bar['high']
        b_l  = bar['low']
        b_c  = bar['close']

        ep = trade.open_price  # entry price

        # --- Áp dụng Break-Even ---
        if cfg.use_be and not trade.be_applied:
            if d == 1:   # BUY: profit khi high đạt open + be_act
                if b_h >= ep + be_act:
                    new_sl = ep + be_lck
                    if new_sl > trade.current_sl:
                        trade.current_sl = new_sl
                        trade.be_applied  = True
            else:        # SELL: profit khi low đạt open - be_act
                if b_l <= ep - be_act:
                    new_sl = ep - be_lck
                    if trade.current_sl == trade.sl or new_sl < trade.current_sl:
                        trade.current_sl = new_sl
                        trade.be_applied  = True

        # --- Áp dụng Trailing Stop ---
        if cfg.use_trail:
            if d == 1:
                if b_h >= ep + tr_st:
                    new_sl = b_h - tr_step
                    if new_sl > trade.current_sl:
                        trade.current_sl = new_sl
            else:
                if b_l <= ep - tr_st:
                    new_sl = b_l + tr_step
                    if trade.current_sl == trade.sl or new_sl < trade.current_sl:
                        trade.current_sl = new_sl

        # --- Kiểm tra EOD force close ---
        if cfg.force_close_eod and not is_session_time(b_dt, cfg):
            # Đóng tại open của bar đầu tiên ngoài session
            cp = b_o
            trade.close_time  = b_dt
            trade.close_price = cp
            trade.gross_profit = calc_pnl(d, trade.lots, ep, cp, cfg)
            trade.reason = "EOD"
            break

        # --- Kiểm tra SL/TP trong bar hiện tại ---
        #
        # Nếu cả SL và TP đều nằm trong range bar:
        #   - BUY uptrend bar (close > open): giả sử TP hit trước
        #   - BUY downtrend bar (close < open): giả sử SL hit trước
        #   Ngược lại cho SELL
        #
        sl_hit = (d == 1 and b_l <= trade.current_sl) or \
                 (d == -1 and b_h >= trade.current_sl)
        tp_hit = (d == 1 and b_h >= trade.tp) or \
                 (d == -1 and b_l <= trade.tp)

        if sl_hit and tp_hit:
            # Cả hai: dùng hướng bar để quyết định
            is_bullish = (b_c > b_o)
            if d == 1:
                tp_first = is_bullish
            else:
                tp_first = not is_bullish

            if tp_first:
                sl_hit = False
            else:
                tp_hit = False

        if tp_hit:
            trade.close_time  = b_dt
            trade.close_price = trade.tp
            trade.gross_profit = calc_pnl(d, trade.lots, ep, trade.tp, cfg)
            trade.reason = "TP"
            break

        if sl_hit:
            # Nếu BE đã active và SL = BE price → reason = BE
            trade.close_time  = b_dt
            trade.close_price = trade.current_sl
            trade.gross_profit = calc_pnl(d, trade.lots, ep, trade.current_sl, cfg)
            if trade.be_applied and abs(trade.current_sl - (ep + be_lck * d)) < cfg.point:
                trade.reason = "BE"
            else:
                trade.reason = "SL" if not trade.be_applied else "TRAIL_SL"
            break

    # Nếu đến cuối dữ liệu mà chưa đóng
    if trade.close_time is None:
        last = df.iloc[-1]
        trade.close_time  = last['datetime']
        trade.close_price = last['close']
        trade.gross_profit = calc_pnl(d, trade.lots, trade.open_price,
                                       trade.close_price, cfg)
        trade.reason = "DATA_END"

    trade.commission  = cfg.commission_usd * trade.lots * 2  # round trip
    trade.net_profit  = trade.gross_profit - trade.commission
    trade.duration_min = int((trade.close_time - trade.open_time).total_seconds() / 60)
    return trade


# =====================================================================
# 10. MAIN BACKTEST LOOP (OHLCV standalone mode)
# =====================================================================
def run_ohlcv_backtest(df: pd.DataFrame, cfg: Config,
                       start_date: Optional[str] = None,
                       end_date:   Optional[str] = None) -> Tuple[List[Trade], List[DayStats]]:
    """
    Vòng lặp chính:
    1. Duyệt qua từng bar M5
    2. Phát hiện signal tại bar i (bar đã đóng)
    3. Vào lệnh tại bar i+1 (bar mới mở)
    4. Mô phỏng lệnh đến khi đóng
    5. Theo dõi risk management (daily loss, consecutive loss)
    """
    trades:    List[Trade]    = []
    day_stats: List[DayStats] = []
    trade_id  = 0

    balance      = cfg.initial_balance
    consec_loss  = 0
    session_stop = False
    cur_day_str  = ""
    day_start_bal = balance
    day_wins = day_losses = 0
    day_pnl  = 0.0

    # Lọc theo ngày test (nếu có)
    if start_date:
        sd = pd.to_datetime(start_date.replace('.', '-'))
        df = df[df['datetime'] >= sd].reset_index(drop=True)
    if end_date:
        ed = pd.to_datetime(end_date.replace('.', '-'))
        df = df[df['datetime'] < ed].reset_index(drop=True)

    open_trade: Optional[Trade] = None
    i = 1  # bắt đầu từ bar[1] (cần bar[0] = bar[i-1] cho conditions)

    while i < len(df) - 1:
        bar  = df.iloc[i]
        b_dt = bar['datetime']
        day_str = b_dt.strftime('%Y.%m.%d')

        # --- Reset ngày mới ---
        if day_str != cur_day_str:
            if cur_day_str:
                day_stats.append(DayStats(
                    date=cur_day_str, start_bal=day_start_bal,
                    trades=day_wins + day_losses,
                    wins=day_wins, losses=day_losses,
                    pnl=day_pnl, stopped=session_stop
                ))
            cur_day_str   = day_str
            day_start_bal = balance
            consec_loss   = 0
            session_stop  = False
            day_wins = day_losses = 0
            day_pnl  = 0.0

        # --- Nếu có lệnh đang mở: kiểm tra close ---
        if open_trade is not None:
            open_trade = simulate_trade(open_trade, df, i, cfg)

            # Khi lệnh đã đóng
            balance += open_trade.net_profit
            day_pnl += open_trade.net_profit
            trades.append(open_trade)

            if open_trade.net_profit >= 0:
                day_wins   += 1
                consec_loss = 0
            else:
                day_losses   += 1
                consec_loss  += 1
                if consec_loss >= cfg.max_consec_loss:
                    session_stop = True

            # Nhảy đến bar tiếp theo sau khi lệnh đóng
            close_idx = df[df['datetime'] == open_trade.close_time].index
            if len(close_idx) > 0:
                i = close_idx[0] + 1
            else:
                i += 1
            open_trade = None
            continue

        # --- Kiểm tra điều kiện mở lệnh mới ---
        if session_stop:
            i += 1; continue

        daily_loss_pct = (day_start_bal - balance) / day_start_bal * 100 if day_start_bal > 0 else 0
        if daily_loss_pct >= cfg.max_daily_loss:
            i += 1; continue

        if not is_session_time(b_dt, cfg):
            i += 1; continue

        spread_pts = bar.get('spread', cfg.default_spread) if hasattr(bar, 'get') else cfg.default_spread
        if isinstance(spread_pts, (int, float)) and spread_pts > cfg.max_spread_pts:
            i += 1; continue

        # --- Phát hiện signal tại bar i ---
        sig, cond = get_signal(df, i, cfg)
        if sig == 0:
            i += 1; continue

        # --- Vào lệnh tại bar i+1 (open của bar tiếp theo) ---
        next_bar = df.iloc[i + 1]
        entry_bar_dt = next_bar['datetime']
        spread_price = spread_pts * cfg.point

        if sig == 1:   # BUY: entry at ASK = open + spread
            entry_price = next_bar['open'] + spread_price
            sl_price = entry_price - cfg.sl_pts * cfg.point
            tp_price = entry_price + cfg.tp_pts * cfg.point
        else:          # SELL: entry at BID = open
            entry_price = next_bar['open']
            sl_price = entry_price + cfg.sl_pts * cfg.point
            tp_price = entry_price - cfg.tp_pts * cfg.point

        lots = calc_lot_size(balance, cfg)
        trade_id += 1

        trade = Trade(
            id          = trade_id,
            open_time   = entry_bar_dt,
            bar_time    = b_dt,             # bar[1] time (signal trigger bar)
            direction   = sig,
            lots        = lots,
            open_price  = entry_price,
            sl          = sl_price,
            tp          = tp_price,
            spread_pts  = spread_pts,
            ema21       = bar['ema21'],
            ema50       = bar['ema50'],
            rsi         = bar['rsi'],
        )

        open_trade = trade
        i += 2   # nhảy qua signal bar + entry bar

    # Đóng ngày cuối
    if cur_day_str:
        day_stats.append(DayStats(
            date=cur_day_str, start_bal=day_start_bal,
            trades=day_wins + day_losses,
            wins=day_wins, losses=day_losses,
            pnl=day_pnl, stopped=session_stop
        ))

    return trades, day_stats


# =====================================================================
# 11. CSV REPLAY MODE (từ Phase-1 log)
# =====================================================================
def run_csv_replay(log_df: pd.DataFrame, ohlcv_df: pd.DataFrame,
                   cfg: Config) -> List[Trade]:
    """
    Replay từ Phase-1 CSV log:
    - Đọc các dòng OPEN → lấy entry price, direction, lots, SL, TP
    - Dùng OHLCV để mô phỏng SL/TP/BE/trail
    - So sánh kết quả với CLOSE rows trong log
    """
    open_rows  = log_df[log_df['event'] == 'OPEN'].copy()
    close_rows = log_df[log_df['event'] == 'CLOSE'].copy()

    trades = []

    for _, row in open_rows.iterrows():
        # Parse OPEN row
        try:
            open_dt  = pd.to_datetime(row['server_time'])
            bar_dt   = pd.to_datetime(row['bar_time'])
            direction = 1 if row['dir'] == 'BUY' else -1
            lots      = float(row['lots'])
            open_px   = float(row['open_price'])
            sl_px     = float(row['sl'])
            tp_px     = float(row['tp'])
            spread    = float(row['spread']) if row['spread'] else cfg.default_spread
            ema21     = float(row['ema21']) if row['ema21'] else 0.0
            ema50     = float(row['ema50']) if row['ema50'] else 0.0
            rsi       = float(row['rsi'])   if row['rsi']   else 50.0
        except (ValueError, KeyError) as e:
            print(f"  Warning: skip OPEN row – {e}")
            continue

        # Tìm vị trí bar entry trong OHLCV
        entry_mask = ohlcv_df['datetime'] >= open_dt
        if not entry_mask.any():
            continue
        entry_idx = ohlcv_df[entry_mask].index[0]

        trade_id = len(trades) + 1
        trade = Trade(
            id         = trade_id,
            open_time  = open_dt,
            bar_time   = bar_dt,
            direction  = direction,
            lots       = lots,
            open_price = open_px,
            sl         = sl_px,
            tp         = tp_px,
            spread_pts = spread,
            ema21      = ema21,
            ema50      = ema50,
            rsi        = rsi,
        )

        # Mô phỏng
        trade = simulate_trade(trade, ohlcv_df, entry_idx, cfg)
        trades.append(trade)

        # So sánh với log
        ticket = str(row.get('ticket', ''))
        matching_close = close_rows[close_rows['ticket'] == ticket]
        if not matching_close.empty:
            cr = matching_close.iloc[0]
            log_net = float(cr['net_profit']) if cr['net_profit'] else None
            if log_net is not None:
                diff = abs(trade.net_profit - log_net)
                if diff > 0.02:
                    print(f"  [DIFF] Trade#{trade_id} {row['dir']} @ {open_px:.5f} "
                          f"| sim={trade.net_profit:.2f} log={log_net:.2f} reason={trade.reason}/{cr.get('reason','?')}")

    return trades


# =====================================================================
# 12. REPORT GENERATION
# =====================================================================
def generate_report(trades: List[Trade], day_stats: List[DayStats],
                    cfg: Config, mt5_final_balance: float = 0.0) -> None:
    """In báo cáo chi tiết và so sánh với MT5."""

    if not trades:
        print("\nNo trades found.")
        return

    total  = len(trades)
    wins   = sum(1 for t in trades if t.net_profit >= 0)
    losses = total - wins
    wr     = wins / total * 100 if total > 0 else 0
    total_pnl = sum(t.net_profit for t in trades)
    final_bal = cfg.initial_balance + total_pnl

    # Drawdown (running equity)
    equity = cfg.initial_balance
    peak   = equity
    max_dd = 0.0
    for t in trades:
        equity += t.net_profit
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak * 100 if peak > 0 else 0
        max_dd  = max(max_dd, dd)

    # Trade duration stats
    durations = [t.duration_min for t in trades if t.duration_min > 0]
    avg_dur   = sum(durations) / len(durations) if durations else 0

    # Reason breakdown
    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1

    # ---- Header ----
    print("\n" + "=" * 65)
    print("  ScalpingEURUSD – Auto Backtest Report")
    print("=" * 65)

    # ---- Summary ----
    summary = [
        ["Initial Balance",   f"${cfg.initial_balance:.2f}"],
        ["Final Balance",     f"${final_bal:.2f}"],
        ["Net P&L",           f"${total_pnl:.2f}"],
        ["Return",            f"{total_pnl/cfg.initial_balance*100:.2f}%"],
        ["Total Trades",      total],
        ["Wins",              f"{wins} ({wr:.1f}%)"],
        ["Losses",            losses],
        ["Max Drawdown",      f"{max_dd:.2f}%"],
        ["Avg Trade Duration", f"{avg_dur:.0f} min"],
    ]
    if mt5_final_balance > 0:
        diff = final_bal - mt5_final_balance
        summary.append(["MT5 Final Balance",  f"${mt5_final_balance:.2f}"])
        summary.append(["Difference vs MT5",  f"${diff:.2f} ({diff/mt5_final_balance*100:.2f}%)"])

    if HAS_TABULATE:
        print(tabulate(summary, headers=["Metric", "Value"], tablefmt="rounded_outline"))
    else:
        for k, v in summary:
            print(f"  {k:<28} {v}")

    # ---- Close reasons ----
    print("\nClose Reason Breakdown:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        wins_r = sum(1 for t in trades if t.reason == reason and t.net_profit >= 0)
        print(f"  {reason:<12} {count:>4} ({pct:.0f}%)  WR={wins_r/count*100:.0f}%")

    # ---- Day stats ----
    if day_stats:
        print(f"\nDaily Summary ({len(day_stats)} trading days):")
        trading_days = [d for d in day_stats if d.trades > 0]
        if trading_days:
            best  = max(trading_days, key=lambda d: d.pnl)
            worst = min(trading_days, key=lambda d: d.pnl)
            print(f"  Best day:   {best.date}  PnL=${best.pnl:.2f}  W/L={best.wins}/{best.losses}")
            print(f"  Worst day:  {worst.date} PnL=${worst.pnl:.2f}  W/L={worst.wins}/{worst.losses}")
            stopped_days = sum(1 for d in day_stats if d.stopped)
            print(f"  Session stopped (consec loss): {stopped_days} days")

    # ---- Last 15 trades ----
    print(f"\nLast {min(15, total)} Trades:")
    trade_rows = []
    for t in trades[-15:]:
        trade_rows.append([
            t.id,
            t.open_time.strftime('%m-%d %H:%M'),
            "BUY" if t.direction == 1 else "SELL",
            f"{t.lots:.2f}",
            f"{t.open_price:.5f}",
            f"{t.close_price:.5f}",
            t.reason,
            f"${t.net_profit:+.2f}",
        ])
    headers = ["#", "OpenTime", "Dir", "Lots", "Entry", "Exit", "Reason", "NetPnL"]
    if HAS_TABULATE:
        print(tabulate(trade_rows, headers=headers, tablefmt="simple"))
    else:
        print("  " + " | ".join(f"{h:>8}" for h in headers))
        for row in trade_rows:
            print("  " + " | ".join(f"{str(v):>8}" for v in row))

    # ---- Export trade list to CSV ----
    out_csv = "backtest_results.csv"
    with open(out_csv, 'w', encoding='utf-8') as f:
        f.write("id,open_time,bar_time,dir,lots,open_price,sl,tp,close_time,"
                "close_price,gross_profit,commission,net_profit,reason,duration_min,"
                "ema21,ema50,rsi,spread_pts,be_applied\n")
        for t in trades:
            f.write(f"{t.id},"
                    f"{t.open_time.strftime('%Y.%m.%d %H:%M:%S')},"
                    f"{t.bar_time.strftime('%Y.%m.%d %H:%M:%S')},"
                    f"{'BUY' if t.direction == 1 else 'SELL'},"
                    f"{t.lots:.2f},{t.open_price:.5f},{t.sl:.5f},{t.tp:.5f},"
                    f"{t.close_time.strftime('%Y.%m.%d %H:%M:%S') if t.close_time else ''},"
                    f"{t.close_price:.5f},{t.gross_profit:.2f},{t.commission:.2f},"
                    f"{t.net_profit:.2f},{t.reason},{t.duration_min},"
                    f"{t.ema21:.5f},{t.ema50:.5f},{t.rsi:.2f},{t.spread_pts:.0f},"
                    f"{'1' if t.be_applied else '0'}\n")
    print(f"\nTrade list exported: {out_csv}")


# =====================================================================
# 13. CLI INTERFACE
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description='ScalpingEURUSD Auto Backtest Engine v1.0'
    )
    parser.add_argument('--mode', choices=['ohlcv', 'csv'], default='ohlcv',
                        help='ohlcv=standalone từ OHLCV, csv=replay từ Phase-1 log')
    parser.add_argument('--data', required=True,
                        help='File OHLCV CSV (MT5 export format)')
    parser.add_argument('--log',
                        help='Phase-1 log CSV (dùng với --mode csv)')
    parser.add_argument('--start', default='',
                        help='Ngày bắt đầu test YYYY.MM.DD, ví dụ: 2025.04.09')
    parser.add_argument('--end', default='',
                        help='Ngày kết thúc test YYYY.MM.DD, ví dụ: 2025.10.08')
    parser.add_argument('--balance', type=float, default=100.0,
                        help='Initial balance (default: 100)')
    parser.add_argument('--risk', type=float, default=1.0,
                        help='Risk per trade %% (default: 1.0)')
    parser.add_argument('--spread', type=float, default=6.0,
                        help='Default spread points nếu CSV không có cột spread (default: 6)')
    parser.add_argument('--mt5-balance', type=float, default=0.0,
                        help='MT5 final balance để so sánh')
    parser.add_argument('--be-lock', type=float, default=30.0,
                        help='Break-Even lock points (default: 30, khớp v1.10 EA)')

    args = parser.parse_args()

    # Build config
    cfg = Config(
        initial_balance = args.balance,
        risk_pct        = args.risk,
        default_spread  = args.spread,
        be_lock_pts     = args.be_lock,
    )

    print(f"\nLoading OHLCV data: {args.data}")
    df = load_mt5_ohlcv(args.data)
    print(f"  Bars loaded: {len(df)} | {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")

    print("Computing indicators (EMA21, EMA50, RSI14)...")
    df = compute_indicators(df, cfg)

    if args.mode == 'csv':
        if not args.log:
            print("ERROR: --mode csv requires --log <phase1_csv>")
            sys.exit(1)
        print(f"Loading Phase-1 log: {args.log}")
        log_df = load_phase1_csv(args.log)
        open_count  = len(log_df[log_df['event'] == 'OPEN'])
        close_count = len(log_df[log_df['event'] == 'CLOSE'])
        print(f"  Events: OPEN={open_count}, CLOSE={close_count}")
        print("Running CSV replay mode...")
        trades = run_csv_replay(log_df, df, cfg)
        day_stats = []

    else:
        print(f"Running standalone OHLCV backtest...")
        print(f"  Period: {args.start or 'from start'} → {args.end or 'to end'}")
        print(f"  Config: SL={cfg.sl_pts}pts TP={cfg.tp_pts}pts "
              f"Risk={cfg.risk_pct}% Balance=${cfg.initial_balance}")
        trades, day_stats = run_ohlcv_backtest(
            df, cfg,
            start_date=args.start or None,
            end_date=args.end   or None,
        )

    print(f"\nSimulation complete: {len(trades)} trades")
    generate_report(trades, day_stats, cfg, mt5_final_balance=args.mt5_balance)


if __name__ == '__main__':
    main()
