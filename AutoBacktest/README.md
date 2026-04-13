# Auto Backtest Engine – ScalpingEURUSD

## Kiến trúc 2 Phase

```
Phase 1 (MQL5 EA)          Phase 2 (Python Engine)
─────────────────          ──────────────────────────────
ScalpingEURUSD.mq5    →    backtest_engine.py
  Chạy Strategy Tester       Đọc CSV log + OHLCV data
  Ghi CSV log:                Re-simulate từng lệnh
    BAR_ANALYSIS              So sánh kết quả vs MT5
    OPEN
    MODIFY_BE/TRAIL
    CLOSE
```

## Cài đặt

```bash
pip install pandas numpy tabulate
```

## Lấy dữ liệu OHLCV từ MT5

1. Mở MT5 → Tools → History Center
2. Chọn EURUSD → M5
3. Right-click → Export → lưu file `eurusd_m5.csv`

Format MT5 export (tab-separated):
```
<DATE>   <TIME>  <OPEN>   <HIGH>   <LOW>    <CLOSE>  <TICKVOL> <VOL> <SPREAD>
2025.04.09  00:05  1.10105  1.10120  1.10095  1.10112  150       0     6
```

## Cách chạy

### Mode 1: Standalone OHLCV (không cần Phase-1 log)
```bash
python backtest_engine.py \
  --mode ohlcv \
  --data eurusd_m5.csv \
  --start 2025.04.09 \
  --end   2025.10.08 \
  --balance 100 \
  --risk 1.0 \
  --mt5-balance 98.45   # (optional) để so sánh vs MT5 result
```

### Mode 2: CSV Replay (từ Phase-1 log)
```bash
python backtest_engine.py \
  --mode csv \
  --data eurusd_m5.csv \
  --log  "SEUR_EURUSD_M5_TESTER_20250409_000000.csv" \
  --balance 100
```
File log ở: `MT5\MQL5\Files\Tester\SEUR_EURUSD_M5_TESTER_*.csv`

## Output

```
=================================================================
  ScalpingEURUSD – Auto Backtest Report
=================================================================
  Initial Balance         $100.00
  Final Balance           $98.45
  Net P&L                 $-1.55
  Total Trades            47
  Wins                    24 (51.1%)
  Max Drawdown            5.23%
  ...

Close Reason Breakdown:
  SL           21  (45%)  WR=0%
  TP           18  (38%)  WR=100%
  BE            5  (11%)  WR=100%
  EOD           3   (6%)  WR=33%

Trade list exported: backtest_results.csv
```

## Lý do kết quả khớp MT5

| Yếu tố | MT5 (Real Tick) | Engine (Bar OHLC) |
|--------|----------------|-------------------|
| EMA | MODE_EMA α=2/(N+1) | ewm(span=N, adjust=False) ✓ |
| RSI | Wilder RMA α=1/N | ewm(alpha=1/N, adjust=False) ✓ |
| Entry BUY | ASK = bid+spread | open + spread*point ✓ |
| Entry SELL | BID | open ✓ |
| Lot size | floor(risk/(SL_pip×10)) | same formula ✓ |
| P&L | (close-open)×dir×lot×100000 | same ✓ |
| Session | VN time = server + (7-GMT) | same ✓ |

Sai số nhỏ do bar-OHLC không có tick-level precision (±1-2 trades trong 1000).

## Bugs đã fix trong ScalpingEURUSD.mq5 v1.10 → v1.11

1. **Zero divide** (line 462): `net / (g_openLots * 10.0)` khi `g_openLots=0`
   → Fix: guard `(g_openLots > 0) ? ... : 0.0`

2. **BE Invalid stops** (line 665): `curSL < newSL` với float imprecision gây loop retry
   → Fix: `NormalizeDouble(newSL - curSL, _Digits) > 0`

3. **BELock too small**: default 5 pts < spread → "Invalid stops"
   → Fix: tăng default lên 30 pts (3 pip, > spread 2 pip Standard)
