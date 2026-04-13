# WORK LOG – ScalpingEURUSD Project
> Cap nhat lan cuoi: 2026-04-13
> Git repo: https://github.com/dinhthao82/Robot

---

## SETUP MOI TREN MAY KHAC

### 1. Clone repo
```bash
git clone https://github.com/dinhthao82/Robot.git
cd Robot
```

### 2. Cai Python packages
```bash
pip install pandas numpy tabulate MetaTrader5
```

### 3. Lay du lieu EURUSD M5 tu MT5
Cach 1 – Python API (can MT5 demo dang chay):
```bash
python - <<'EOF'
import MetaTrader5 as mt5, pandas as pd
mt5.initialize()
rates = mt5.copy_rates_from_pos('EURUSD', mt5.TIMEFRAME_M5, 0, 80000)
df = pd.DataFrame(rates)
df['datetime'] = pd.to_datetime(df['time'], unit='s')
df = df[['datetime','open','high','low','close','tick_volume']]
df.to_csv('AutoBacktest/eurusd_m5.csv', index=False)
print(f"Saved {len(df)} bars: {df.datetime.iloc[0]} to {df.datetime.iloc[-1]}")
mt5.shutdown()
EOF
```
Cach 2 – Export tu MT5 History Center:
- MT5 -> Tools -> History Center -> EURUSD -> M5 -> Export
- Luu thanh `AutoBacktest/eurusd_m5.csv`
- Format: DATE,TIME,OPEN,HIGH,LOW,CLOSE,TICKVOL (tab-separated)
- NOTE: cap nhat lai ham `load_data()` trong backtest_engine.py neu format khac

### 4. Cai ScalpingEURUSD.mq5 vao MT5
- Copy `Robot/ScalpingEURUSD.mq5` vao `MT5\MQL5\Experts\`
- Compile trong MetaEditor (F7)
- Version hien tai: v1.10 (file MQ5) / v1.11 (logic da fix trong comment README)

---

## TONG KET TIEN DO

### Phase 1: Tao EA + Phase-1 Logging [DONE]
- `Robot/ScalpingEURUSD.mq5` – EA scalping EMA21/EMA50 + RSI14
- Log CSV moi lenh: OPEN, MODIFY_BE, MODIFY_TRAIL, CLOSE
- File log o: `MT5\MQL5\Files\Tester\SEUR_EURUSD_M5_TESTER_*.csv`

### Phase 2: Auto Backtest Engine [DONE]
- `AutoBacktest/backtest_engine.py` – Re-simulate tu OHLCV
- `AutoBacktest/compare_log.py` – So sanh Python vs MT5 log
- Ket qua: **11/11 lenh khop 100%**, P&L -$2.99 = MT5 confirmed

### Phase 3: Parameter Optimization [DONE – Phase 1+2]
- `AutoBacktest/run_optimize.py` – 2-phase grid search
- Tim duoc: **WR=76.8%** voi config sau:
  ```
  SL=25 TP=25 (RR 1:1)
  RSI BUY: 60-60, RSI SELL: 33-48
  EMA buffer: 25pts, EMA50 touch required
  Min body: 20pts, Session: 16-22VN
  ```
- **VAN DE**: WR 76% la ao – 76% exit la BE (kiem ~$0.03/lenh), chi 2/280 = 0.7% la TP that
- Ket qua that su: P&L = -$9.36, PF = 0.42 (THUA LON)

### Phase 4: Profitability Optimization [DANG LAM – CAN TIEP TUC]
- `AutoBacktest/optimize_v2.py` – Tim config CO LOI NHUAN that su
- TP Sweep da chay (SL=25 co dinh, TP=8..50, khong BE):
  ```
  TP=25p  RR 1:1.00  WR=47.7%  P&L=-$1.73   PF=0.95  (THUA)
  TP=32p  RR 1:1.28  WR=43.8%  P&L=-$0.04   PF=1.00  (hoa von)
  TP=40p  RR 1:1.60  WR=41.3%  P&L=+$4.78   PF=1.13  (co lai)
  TP=50p  RR 1:2.00  WR=38.3%  P&L=+$8.67   PF=1.23  (tot nhat)
  ```
- **KET LUAN**: Entry hien tai (EMA21/50 + RSI + pullback EMA50)
  KHONG the dat WR>70% profitable voi RR 1:1.
  Strategy nay hoat dong tot nhat o RR 1:2 (SL=25, TP=50).

---

## VIEC CAN LAM TIEP (PRIORITY ORDER)

### [1] URGENT – Optimize RR 1:2 (SL=25, TP=50) [CAN CHAY]
Tim RSI/session/filter toi uu cho config co lai (TP=50, SL=25).
```bash
python AutoBacktest/optimize_v2.py
```
Sau do sua Grid de focus vao SL=[20,25,30], TP=[40,45,50,60]:
- Trong `optimize_v2.py`, sua `GRID_A`:
  ```python
  GRID_A = {
      'sl_pts':      [20, 25, 30],
      'tp_pts':      [35, 40, 45, 50, 60],
      'ema50_touch': [True, False],
      'min_body':    [0, 10, 15, 20],
      'sess_start':  [14, 15, 16],
      'sess_end':    [19, 20, 21, 22],
  }
  ```
- Chay Phase A (2016 combos, ~60s), Phase B (31500 combos/config, ~15 min)
- Target: PF >= 1.2, WR >= 38%

### [2] Cap nhat ScalpingEURUSD.mq5
Sau khi tim duoc config toi uu, update cac input trong EA:
- Them 2 input moi (chua co trong mq5 hien tai):
  ```mql5
  input bool   InpRequireEMA50Touch = true;   // Require pullback to EMA50
  input int    InpMinBodyPts        = 20;      // Min candle body (pts)
  ```
- Update default values:
  ```mql5
  input int    InpSL         = 25;   // Stop Loss pts
  input int    InpTP         = 50;   // Take Profit pts (RR 1:2)
  input int    InpBEActivate = 0;    // Disable BE (pure TP/SL)
  input int    InpBELock     = 0;
  input int    InpRSIBuyMin  = [best from optimize]
  input int    InpRSIBuyMax  = [best from optimize]
  ...
  ```

### [3] Validate tren MT5 Strategy Tester
- Chay Strategy Tester voi params moi
- So sanh P&L/WR/DD voi Python backtest
- Neu khop (±5%), config la chinh xac

### [4] (Optional) Them filter moi de tang WR
Neu muon dat WR>60% voi RR 1:2:
- **ATR filter**: Chi vao lenh khi ATR14 > threshold (du bien dong)
- **Higher timeframe trend**: EMA H1 phai cung chieu voi M5
- **News filter**: Tranh 30min truoc/sau NFP, CPI, FOMC
- **ADX filter**: ADX > 20 (co xu huong ro)

---

## THONG SO CHIEN LUOC HIEN TAI

### Best config tim duoc (WR cao nhat – NHUNG THUA TIEN):
```
SL=25pts  TP=25pts  RR 1:1
RSI BUY:  60-60  |  RSI SELL: 33-48
EMA buffer: 25pts, EMA50 touch=True, MinBody=20pts
Session: 16:00-22:00 VN
Trades: 280  WR: 76.8%  P&L: -$9.36  PF: 0.42
BE exits: 213/280 (76%)  ← WR ao!
```

### Best config tim duoc (CO LOI NHUAN – nen dung):
```
SL=25pts  TP=50pts  RR 1:2
RSI BUY:  60-60  |  RSI SELL: 33-48
EMA buffer: 25pts, EMA50 touch=True, MinBody=20pts
Session: 16:00-22:00 VN
Trades: 248  WR: 38.3%  P&L: +$8.67  PF: 1.23
(Chua optimize RSI/session cho TP=50 – se tang P&L them)
```

---

## CAU TRUC FILE

```
Robot/
  ScalpingEURUSD.mq5      EA chinh – v1.10 (can update input)
  GridBot_EUR_USD.mq5     Bot khac
  NewBotGold*.mq5         Bot Gold variants
  NewBotEURUSD_TwoWay.mq5

AutoBacktest/
  backtest_engine.py      Engine re-simulate tu OHLCV/CSV log
  compare_log.py          So sanh MT5 log vs Python (fix 11/11)
  run_optimize.py         Optimizer phase 1+2 (da tim WR=76.8%)
  optimize_v2.py          Optimizer v2 focus profit (dang chay)
  README.md               Huong dan chi tiet

agent.py / app.py / engine.py   Python trading agent (phu)
requirements.txt
```

---

## LENH HAY DUNG

```bash
# Chay backtest (OHLCV mode)
cd AutoBacktest
python backtest_engine.py --mode ohlcv --data eurusd_m5.csv \
  --start 2025.04.09 --end 2026.04.09 --balance 100

# So sanh MT5 log vs Python
python compare_log.py --log "SEUR_EURUSD_M5_TESTER_*.csv" --data eurusd_m5.csv

# Chay optimizer goc (WR focus)
python run_optimize.py

# Chay optimizer v2 (profit focus)
python optimize_v2.py
```

---

## GHI CHU KY THUAT

### Tai sao WR 76.8% la ao?
Break-Even (BE) fires tai 12.5pts (50% SL=25). 76% lenh dat 12.5pts
roi retrace ve BE stop tai +3pts (~$0.03/lenh). Chi dem la "win" theo
code nhung thuc te khong kiem tien. Real TP (25pts) chi dat 2/280 = 0.7%.

### Cong thuc hoa von WR (khong BE):
- RR 1:1 -> can WR > 50% -> dat duoc 47.7% (THUA)
- RR 1:2 -> can WR > 33% -> dat duoc 38.3% (CO LAI, PF=1.23)

### Indicator formulas (khop MT5):
- EMA: `ewm(span=N, adjust=False)`
- RSI: `ewm(alpha=1/14, adjust=False)` (Wilder smoothing)
- Entry BUY: `open[i+1] + 6*POINT` (bao gom spread 0.6pip)
- Entry SELL: `open[i+1]` (no spread added)
- Lot: `floor(balance * risk% / (SL_pts * 10) / 0.01) * 0.01`
