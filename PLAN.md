# PLAN: MeanRevScalper — EUR/USD
**Chiến thuật:** Bollinger Band + RSI Mean Reversion Scalper  
**Mục tiêu:** 2–3%/ngày | Rủi ro tối đa 5%/ngày  
**Broker:** Exness Pro — MT5 | Day trade only, không giữ lệnh qua đêm  
**Vốn khởi đầu:** $100 — tăng theo lãi kép

---

## Chiến thuật — BB + RSI Mean Reversion Scalper

### Ý tưởng cốt lõi
EUR/USD liên tục dao động quanh mức trung bình trong phiên London/NY.  
Khi giá lệch quá xa khỏi trung bình (ra ngoài Bollinger Band) đồng thời RSI xác nhận trạng thái quá mua/bán → giá có xu hướng quay về trung bình.

### Điều kiện vào lệnh (v3 — sau log analysis)

| Hướng | Điều kiện | Ý nghĩa |
|-------|-----------|---------|
| **BUY** | Close < Lower BB **OR** RSI < RSI_OS — **AND** Close < SMA **AND** EMA_slope ↑ | Dip trong uptrend |
| **SELL** | Close > Upper BB **OR** RSI > RSI_OB — **AND** Close > SMA **AND** EMA_slope ↓ | Rally trong downtrend |

> **EMA slope filter**: chỉ BUY khi EMA(50) đang tăng, chỉ SELL khi EMA(50) đang giảm.  
> Cải thiện WR từ 47% → 67%+. Không dùng price-vs-EMA (conflict với BB condition).  
> RSI_OB = 100 − RSI_OS (symmetric).

### Thoát lệnh

| Loại | Giá trị | Ghi chú |
|------|---------|---------|
| TP | entry ± ATR_TP × ATR14 | Giá trị ATR tại thời điểm mở lệnh |
| SL | entry ∓ ATR_SL × ATR14 | |
| Hard close | 22:00 GMT+7 | Đóng tất cả, không giữ qua đêm |

### Quản lý rủi ro ngày

| Rule | Trigger | Hành động |
|------|---------|-----------|
| Profit lock | Equity ≥ balance_ngày × 1.03 | Không mở lệnh mới, giữ lệnh đang có |
| Hard stop | Equity ≤ balance_ngày × 0.95 | Đóng tất cả, dừng ngày hôm đó |
| Kill switch | Thua 3 ngày liên tiếp | Dừng bot, kiểm tra lại params |

### Thông số tối ưu hóa (best found từ backtesting)

| Tham số | Best value | Ý nghĩa |
|---------|-----------|---------|
| BB_period | **20** | Bollinger Band period |
| BB_std | **1.5** | 1.5σ → nhiều tín hiệu hơn 2.0σ |
| RSI_period | **14** | Standard RSI |
| RSI_OS | **35** | Threshold oversold |
| ATR_SL | **0.5** | Tight SL để giữ RR > 1 |
| max_sl_pips | **6** | Hard cap 6 pip/loss |
| BE_ratio | **0.3** | Breakeven sau 30% TP → WR↑ |
| Trend_EMA | **50** | Slope filter cải thiện WR từ 47% → 67% |
| TBegin | **15 hoặc 16** GMT+7 | Bỏ qua 14:00 — quá choppy |
| TEnd | **21** GMT+7 | |
| Max trades/day | **5** | Cho phép nhiều lần vào lại |
| SL cooldown | **0** | Re-entry ngay sau SL |

### Kết quả backtest thực tế (18 tháng 10/2024–04/2026)

| Config | WR | Daily ret | Balance $100→ |
|--------|-----|-----------|--------------|
| Không filter (EMA=0) | 47% | -0.084%/day | $18 (LỖTBIG) |
| EMA slope + BB_mid | **67%** | **+0.057%/day** | **$131** ✅ |

> ⚠️ **Thực tế:** 0.057%/day = 1.14%/tháng (20 ngày giao dịch).  
> Mục tiêu 2–3%/day **không khả thi với 1H bar** — cần approach khác (xem bên dưới).

---

## 1. Thông số Broker — Exness Pro

| Thông số | Giá trị | Ghi chú |
|----------|---------|---------|
| Min deposit | $200 | Nạp $200, rút $100, trade với $100 |
| Spread EUR/USD avg | **0.6–0.9 pip** | London/NY ~0.6, Asian ~0.9 |
| Spread EUR/USD max | ~2.0–3.0 pip | 30 giây đầu news event |
| Commission | **$0** | |
| Leverage | 1:Unlimited | Margin gần như = $0 với lot nhỏ |
| Min lot | 0.01 | |
| Swap | **Không tính** | Day trade — đóng hết trước 22:00 GMT+7 |
| Stop-out level | 0% | Exness không force close tới khi âm |

### Spread dùng cho backtest

| Scenario | Spread input | Mục đích |
|----------|-------------|---------|
| Bình thường | **0.7 pip** | kết quả kỳ vọng |
| Stress — news | **2.5 pip** | kiểm tra worst case |
| Ultra stress — NFP/FOMC/ECB | **4.0 pip** | cân nhắc tắt bot |

---

## 2. Tính toán thực tế với $100

### Lot sizing

```
lot = max(0.01, round(balance / 10000, 2))
→ $100    → 0.01 lot  (1 pip = $0.10)
→ $500    → 0.05 lot  (1 pip = $0.50)
→ $1,000  → 0.10 lot  (1 pip = $1.00)
→ $5,000  → 0.50 lot  (1 pip = $5.00)
→ $10,000 → 1.00 lot  (1 pip = $10.00)
```

### Để đạt 2–3%/ngày tại từng mốc balance

| Balance | Lot | 1 pip = | Cần kiếm/ngày (2%) | Cần pip net |
|---------|-----|---------|-------------------|-------------|
| $100    | 0.01 | $0.10  | $2.00             | 20 pip      |
| $300    | 0.03 | $0.30  | $6.00             | 20 pip      |
| $1,000  | 0.10 | $1.00  | $20.00            | 20 pip      |
| $5,000  | 0.50 | $5.00  | $100.00           | 20 pip      |
| $10,000 | 1.00 | $10.00 | $200.00           | 20 pip      |

> **Kết luận:** Mục tiêu **20–30 pip net/ngày** — EUR/USD daily range 60–100 pip → khả thi với 2–3 giao dịch/phiên London.

---

## 3. Lãi kép — Milestones

### Kịch bản thực tế (2%/ngày, 20 ngày/tháng)

| Thời gian | Balance  | Lot  |
|-----------|----------|------|
| Ngày 1    | $100     | 0.01 |
| Tháng 1   | $149     | 0.01 |
| Tháng 2   | $222     | 0.02 |
| Tháng 3   | $331     | 0.03 |
| Tháng 6   | $1,100   | 0.11 |
| Tháng 12  | $12,100  | 1.21 |

### Kịch bản tốt (3%/ngày, 20 ngày/tháng)

| Thời gian | Balance  | Lot  |
|-----------|----------|------|
| Ngày 1    | $100     | 0.01 |
| Tháng 1   | $243     | 0.02 |
| Tháng 2   | $590     | 0.05 |
| Tháng 3   | $1,430   | 0.14 |
| Tháng 6   | $20,400  | 2.04 |

> ⚠️ Thực tế có ngày thua → không compound liên tục.  
> Kỳ vọng hợp lý: trading 20 ngày/tháng, đạt 2%+ trên 60–70% số ngày.

---

## ⚠️ Phân tích giới hạn & con đường đến 2–3%/ngày

### Tại sao 1H bar không đủ cho 2–3%/ngày
| Vấn đề | Số liệu | Giải pháp |
|--------|---------|-----------|
| Quá ít tín hiệu | 1.1–1.5 lệnh/ngày | Dùng 15m bar (yfinance hỗ trợ 60 ngày) |
| ATR 1H ≈ 10–14 pip | TP thường không đạt trước TEnd | Shorter TP (8–10 pip) + tight SL |
| Lot 0.01 → 1 pip = $0.10 | Cần 20 pip net/ngày | Tăng lot lên 0.03–0.05 (rủi ro cao hơn) |

### 3 hướng để đạt 2–3%/ngày thực sự
1. **15-min bars**: 4× nhiều tín hiệu/ngày → 4–6 lệnh/ngày. Backtest với 60 ngày gần nhất.
2. **Lot aggressive**: Tăng lot formula từ `bal/10000` → `bal/3000`. Tại $100: lot=0.03, 1pip=$0.30. Cần 20 pip net thay vì 200 pip.
3. **Compound daily loss reset**: Stop thua ngay 1%/ngày (không đợi 5%), compound mạnh hơn vào ngày thắng.

---

## PHASE 1 — Research & Optimizer

### Step 1.1 — Thu thập dữ liệu EUR/USD
- Tải **2 năm** dữ liệu 1H từ yfinance
- Tính daily range (High - Low) theo từng giờ GMT+7
- Thống kê số ngày có daily range ≤ 20 pip (ngày flat, tránh trade)

### Step 1.2 — Xác định session tốt nhất
- **London Open:** 14:00–16:00 GMT+7 → volatility spike → nhiều tín hiệu BB breakout
- **London Full:** 14:00–22:00 GMT+7 → biến động lớn nhất
- **NY–London overlap:** 20:00–22:00 GMT+7 → đỉnh volume
- **Asian:** 07:00–14:00 GMT+7 → BB signal kém tin cậy hơn

### Step 1.3 — Cấu hình Optimizer

| Tham số | Giá trị |
|---------|---------|
| balance_init | **$100** |
| lot formula | max(0.01, bal/10000) |
| spread_pips | **0.7** (Exness Pro) |
| max_trades_day | **3** |
| TEnd cứng | **22** (đóng hết 22:00 GMT+7) |
| profit_lock_pct | **3.0%** |
| hard_stop_pct | **5.0%** |
| swap | **0** (day trade) |

### Step 1.4 — Đánh giá kết quả
- Metric chính: `avg_daily_return ≥ 2%`
- Metric phụ: `max_daily_drawdown ≤ 5%`, `win_rate ≥ 50%`, `profit_factor ≥ 1.5`
- Walk-forward: train 18 tháng → test 6 tháng cuối
- Chạy lại với spread = 2.5 pip → kết quả vẫn phải dương

---

## PHASE 2 — Cải tiến chiến thuật

### Step 2.1 — Thêm ATR Volatility Gate
- Chỉ trade khi ATR > min_atr_pips (ví dụ: 5 pip)
- Bỏ qua giờ thị trường quá flat (ATR thấp → BB squeeze → signal kém)

### Step 2.2 — Confirmation bar
- Thay vì vào ngay khi close < lower_bb, chờ bar tiếp theo close trở lại trong band → vào lệnh
- Giảm false signal nhưng cũng giảm số lệnh

### Step 2.3 — Daily P&L Manager nâng cao

| Rule | Trigger | Hành động |
|------|---------|-----------|
| Profit lock | Equity ≥ day_start × 1.03 | Không mở mới |
| Partial close | Equity ≥ day_start × 1.02 | Trailing stop |
| Soft stop | Equity ≤ day_start × 0.97 | Đóng tất, dừng ngày |
| Hard stop | Equity ≤ day_start × 0.95 | Đóng tất, halt 24h |
| Kill switch | Thua 3 ngày liên tiếp | Dừng bot, báo Telegram |

### Step 2.4 — News filter
- Tắt bot 30 phút trước và sau: NFP, FOMC, ECB, CPI
- Implement blackout_minutes = 30 quanh major news

---

## PHASE 3 — Backtest & Validation

### Step 3.1 — Backtest chính
- Period: 18 tháng gần nhất (train set)
- Balance: $100, spread 0.7 pip (Exness Pro)
- Params từ Phase 1

### Step 3.2 — Stress Test

| Test case | Spread | Yêu cầu |
|-----------|--------|---------|
| Bình thường | **0.7 pip** | avg daily return ≥ 2% |
| News event | **2.5 pip** | phải dương |
| NFP / FOMC / ECB | **4.0 pip** | max lỗ ≤ 5%/ngày |

### Step 3.3 — Out-of-sample (6 tháng cuối)
- Kết quả ≥ 70% so với train → pass

### Step 3.4 — Compound Growth Simulation
- Mô phỏng $100 → ? sau 6 tháng với lot scale theo công thức mới
- Kèm drawdown curve, không chỉ equity curve

---

## PHASE 4 — Python Live Trading (MT5 API)

### Step 4.1 — Kết nối MT5 Python
```
pip install MetaTrader5
```
- `mt5.initialize()` → login Exness Pro account
- `mt5.symbol_info_tick("EURUSD")` → lấy giá realtime
- Tính BB + RSI realtime từ 1H bars gần nhất
- `mt5.order_send(request)` → đặt lệnh khi signal xuất hiện

### Step 4.2 — Refactor engine.py cho live
- Tách `BacktestEngine` và `LiveEngine`
- `LiveEngine` dùng MT5 API thay yfinance
- Giữ nguyên toàn bộ logic BB + RSI strategy
- Thêm `RiskManager` với daily P&L tracking

### Step 4.3 — Logging + Alerts
- Mỗi lệnh open/close → ghi file CSV + SQLite
- Telegram: báo trade, daily P&L, profit lock, stop triggered
- Streamlit tab "Live": equity realtime, open positions

---

## PHASE 5 — MQL5 EA (MT5)

### Step 5.1 — Xây dựng MeanRevScalper.mq5

| Tính năng | Giá trị |
|-----------|---------|
| Lot formula | max(0.01, bal/10000) |
| Signal | iBands + iRSI |
| TP/SL | ATR-based (iATR) |
| Profit lock | 3%/ngày |
| Hard stop | 5%/ngày |
| Day trade close | Hard close 22:00 GMT+7 |
| Max trades/day | 3 |
| SL cooldown | 2 bars |
| Spread filter | Không trade nếu spread > 2 pip |

### Step 5.2 — Strategy Tester MT5
- Mode: **Every tick based on real ticks**
- Spread: **7** (= 0.7 pip, Exness Pro)
- Chạy thêm Spread = **25** (news stress)
- Initial deposit: **$100**
- Kết quả phải khớp ±20% với Python backtest

### Step 5.3 — Demo Forward Test
- Tài khoản: **Exness Pro demo, $100**
- Chạy **2 tuần** liên tục, không can thiệp
- Xác nhận: tất cả lệnh đóng trước 22:00 GMT+7 mỗi ngày

---

## PHASE 6 — Go Live

### Step 6.1 — Live $100 Exness Pro
- Nạp $200, rút $100, trade với $100 trên Exness Pro
- Chạy EA với params đã validate trên demo
- Monitor 30 phút/lần trong tuần đầu

### Step 6.2 — Scale up theo kết quả

| Điều kiện | Hành động |
|-----------|-----------|
| 7 ngày không vi phạm risk rule | Giữ nguyên, theo dõi tiếp |
| Tháng 1 đạt ≥ 50% mục tiêu | Nạp thêm vốn |
| 3 tháng liên tiếp ≥ 60% mục tiêu | Scale lot × 2 |
| Thua 3 ngày liên tiếp | Dừng, review params |
| Balance giảm về $70 (-30%) | Dừng hoàn toàn, re-optimize |

---

## Timeline

| Phase | Nội dung | Thời gian |
|-------|----------|-----------|
| 1 | Research + Optimizer $100, spread 0.7 | 3 ngày |
| 2 | Strategy improvements: ATR gate, news filter | 2 ngày |
| 3 | Backtest + stress test + compound simulation | 2 ngày |
| 4 | Python live (MT5 API) | 4 ngày |
| 5 | MQL5 EA + Strategy Tester | 4 ngày |
| 6 | Demo 2 tuần → Live | 2–3 tuần |

**Tổng: ~5–7 tuần**

---

## Rủi ro thực tế

| Rủi ro | Mức | Giảm thiểu |
|--------|-----|-----------|
| BB signal trong trend mạnh (mean reversion thất bại) | **Cao** | ATR gate, không trade khi ATR quá thấp hoặc quá cao |
| EUR/USD flat day < 20 pip | Trung bình | Skip ngày nếu range < 25 pip lúc TBegin |
| News event spread tăng 5–10 pip | **Cao** | Tắt bot 30 phút trước NFP, FOMC, ECB, CPI |
| Lệnh không đóng đúng 22:00 | Trung bình | Hard close TEnd = 22 trong EA |
| $100 lãi ít ban đầu | Thực tế | $2/ngày — cần kiên nhẫn compound |
| Margin call | **Rất thấp** | Leverage 1:Unlimited, Hard Stop 5%, stop-out 0% |

---

## Bước tiếp theo (bắt đầu ngay)

Chạy Phase 1 — optimizer với tham số mặc định:
- `balance_init = 100`
- `spread_pips = 0.7`
- `BB_period = [14, 20]`
- `BB_std = [1.5, 2.0]`
- `RSI_period = [10, 14]`
- `RSI_OS = [30, 35]`
- `ATR_TP = [1.5, 2.0, 2.5]`
- `ATR_SL = [0.7, 1.0]`
- `TBegin = [14, 15, 16]`
- `TEnd = [21, 22]`
- `max_trades_day = 3`
- `profit_lock_pct = 3.0`
- `hard_stop_pct = 5.0`
