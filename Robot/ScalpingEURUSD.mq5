//+------------------------------------------------------------------+
//|                                              ScalpingEURUSD.mq5 |
//|               Scalping EA – EMA21/50 + RSI14 – EUR/USD Exness  |
//|                                                                  |
//| Strategy:                                                        |
//|   - Trend: EMA21 > EMA50 (BUY) / EMA21 < EMA50 (SELL)         |
//|   - Entry: Giá pullback về chạm EMA21, bounce xác nhận          |
//|   - Filter: RSI14 trong vùng hợp lý (không OB/OS)              |
//|   - Session: 14:00–22:00 VN (London + NY)                       |
//|   - Risk: 1% per trade, max 3% daily loss, dừng sau 2 thua      |
//|                                                                  |
//| Phase 1 – Auto Backtest Logging:                                 |
//|   Ghi CSV đầy đủ để auto backtest tái tạo chính xác:            |
//|   INIT | DAY_START | BAR_ANALYSIS | SKIP | OPEN                 |
//|   MODIFY_BE | MODIFY_TRAIL | CLOSE | SESSION_STOP               |
//|   DAY_END | FINAL                                                |
//|   File: MQL5\Files\SEUR_<Symbol>_<TF>_<datetime>.csv           |
//+------------------------------------------------------------------+
#property copyright "ScalpingEURUSD v1.10"
#property version   "1.10"
#property description "EMA21+EMA50+RSI14 Scalping – EUR/USD – Exness | AutoBacktest Log P1"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\DealInfo.mqh>

CTrade        g_trade;
CPositionInfo g_pos;
CDealInfo     g_deal;

//--- ===== INPUT PARAMETERS =====
input group "=== SESSION (VN TIME GMT+7) ==="
input int    InpSessionStart  = 14;    // Giờ bắt đầu (VN)
input int    InpSessionEnd    = 22;    // Giờ kết thúc (VN)
input int    InpServerGMT     = 3;     // Server GMT offset (Exness = 3)
input bool   InpForceCloseEOD = true;  // Đóng lệnh khi hết phiên

input group "=== RISK MANAGEMENT ==="
input double InpRiskPercent   = 1.0;   // % balance risk mỗi lệnh
input double InpMaxDailyLoss  = 3.0;   // Dừng ngày khi mất X% balance
input int    InpMaxConsecLoss = 2;     // Dừng phiên sau N lệnh thua liên tiếp
input double InpMaxSpread     = 20;    // Spread tối đa (points, 20 = 2 pip Standard)

input group "=== STOP LOSS / TAKE PROFIT ==="
input double InpStopLoss      = 50;    // SL (points) – mặc định 5 pip
input double InpTakeProfit    = 100;   // TP (points) – mặc định 10 pip
input bool   InpUseBreakEven  = true;  // Bật Break-Even
input double InpBEActivate    = 60;    // Kích hoạt BE khi lãi X points
input double InpBELock        = 30;    // Lock thêm X points sau BE (>=spread để tránh invalid stops)
input bool   InpUseTrailing   = true;  // Bật Trailing Stop
input double InpTrailingStart = 80;    // Bắt đầu trailing khi lãi X points
input double InpTrailingStep  = 30;    // Bước trailing (points)

input group "=== EMA SETTINGS ==="
input int    InpFastEMA       = 21;    // EMA nhanh
input int    InpSlowEMA       = 50;    // EMA chậm
input double InpEMABuffer     = 30;    // Vùng chạm EMA21 (±X points)
input ENUM_TIMEFRAMES InpTF   = PERIOD_M5; // Timeframe chiến lược

input group "=== RSI SETTINGS ==="
input int    InpRSIPeriod     = 14;    // RSI period
input double InpRSIBuyMin     = 45.0;  // RSI BUY tối thiểu
input double InpRSIBuyMax     = 70.0;  // RSI BUY tối đa (tránh overbought)
input double InpRSISellMin    = 30.0;  // RSI SELL tối thiểu (tránh oversold)
input double InpRSISellMax    = 55.0;  // RSI SELL tối đa

input group "=== EA SETTINGS ==="
input int    InpMagic         = 20260411; // Magic Number
input string InpComment       = "SEUR";   // Comment lệnh

input group "=== AUTO BACKTEST LOG ==="
input bool   InpLogEnabled    = true;  // Bật ghi log CSV
input bool   InpLogLiveAlso   = false; // Ghi log cả khi chạy live (không chỉ tester)

//+------------------------------------------------------------------+
//| ===== GLOBALS =====                                              |
//+------------------------------------------------------------------+
int      g_hFastEMA, g_hSlowEMA, g_hRSI;
double   g_bufFast[], g_bufSlow[], g_bufRSI[];

// Trạng thái phiên / ngày
int      g_consecLoss    = 0;
double   g_dayStartBal   = 0.0;
bool     g_sessionStopped = false;
datetime g_lastBar       = 0;
datetime g_lastDay       = 0;

// Thống kê tổng
int    g_totalWin  = 0, g_totalLoss  = 0;
double g_totalPnL  = 0.0;

// Thống kê ngày
int    g_dayWin    = 0, g_dayLoss    = 0;
double g_dayPnL    = 0.0;

//+------------------------------------------------------------------+
//| ===== LOGGING MODULE – Phase 1 Auto Backtest ===================|
//+------------------------------------------------------------------+

// CSV Header (26 cột):
// EVENT | SERVER_TIME | BAR_TIME | MODE | TICKET | DIR | LOTS
// OPEN_PRICE | SL | TP | NEW_SL | CLOSE_PRICE
// EMA21 | EMA50 | RSI | SPREAD
// PROFIT | SWAP | COMMISSION | NET_PROFIT
// BALANCE | EQUITY | OPEN_TIME | DURATION_MIN | REASON | NOTE

#define LOG_SEP    ";"
#define LOG_HEADER "EVENT;SERVER_TIME;BAR_TIME;MODE;TICKET;DIR;LOTS;OPEN_PRICE;SL;TP;NEW_SL;CLOSE_PRICE;EMA21;EMA50;RSI;SPREAD;PROFIT;SWAP;COMMISSION;NET_PROFIT;BALANCE;EQUITY;OPEN_TIME;DURATION_MIN;REASON;NOTE"

int      g_logHandle  = INVALID_HANDLE;
string   g_logFile    = "";
bool     g_logActive  = false;

// Theo dõi lệnh đang mở (để tính duration + lưu open context cho CLOSE log)
// EA chỉ giữ 1 lệnh tại 1 thời điểm → dùng biến đơn
ulong    g_openTicket    = 0;
datetime g_openTime      = 0;
double   g_openPrice     = 0.0;
int      g_openDir       = 0;
double   g_openLots      = 0.0;
double   g_openSL        = 0.0;
double   g_openTP        = 0.0;
double   g_openSpread    = 0.0;
double   g_openEMA21     = 0.0;
double   g_openEMA50     = 0.0;
double   g_openRSI       = 0.0;

//--- Hàm lấy tên TF ngắn gọn
string TFName(ENUM_TIMEFRAMES tf)
{
    switch(tf)
    {
        case PERIOD_M1:  return "M1";
        case PERIOD_M5:  return "M5";
        case PERIOD_M15: return "M15";
        case PERIOD_M30: return "M30";
        case PERIOD_H1:  return "H1";
        case PERIOD_H4:  return "H4";
        case PERIOD_D1:  return "D1";
        default:         return "TF" + IntegerToString((int)tf);
    }
}

//--- Khởi tạo log file
bool LogOpen()
{
    bool isTester = (bool)MQLInfoInteger(MQL_TESTER);
    if(!InpLogEnabled) return false;
    if(!isTester && !InpLogLiveAlso) return false;

    string mode    = isTester ? "TESTER" : "LIVE";
    string tfStr   = TFName(InpTF);
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    string stamp   = StringFormat("%04d%02d%02d_%02d%02d%02d",
                                  dt.year, dt.mon, dt.day,
                                  dt.hour, dt.min, dt.sec);
    g_logFile = StringFormat("SEUR_%s_%s_%s_%s.csv", _Symbol, tfStr, mode, stamp);

    g_logHandle = FileOpen(g_logFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
    if(g_logHandle == INVALID_HANDLE)
    {
        PrintFormat("⚠️ Log: không mở được file %s (err=%d)", g_logFile, GetLastError());
        return false;
    }

    // Ghi header
    FileWriteString(g_logHandle, LOG_HEADER + "\n");
    FileFlush(g_logHandle);
    g_logActive = true;
    PrintFormat("📝 Log file: %s", g_logFile);
    return true;
}

//--- Ghi 1 dòng CSV (đã có \n cuối)
void LogRow(string line)
{
    if(!g_logActive || g_logHandle == INVALID_HANDLE) return;
    FileWriteString(g_logHandle, line + "\n");
    FileFlush(g_logHandle);
}

//--- Helper: format price 5 chữ số
string FP(double v)  { return StringFormat("%.5f", v); }
string F2(double v)  { return StringFormat("%.2f", v); }
string F4(double v)  { return StringFormat("%.4f", v); }
string TS(datetime t){ return (t == 0) ? "" : TimeToString(t, TIME_DATE | TIME_SECONDS); }
string IB(bool b)    { return b ? "1" : "0"; }

//--- MODE column
string LogMode()
{
    return (bool)MQLInfoInteger(MQL_TESTER) ? "TESTER" : "LIVE";
}

//--- EVENT: INIT – toàn bộ input params
void Log_Init()
{
    if(!g_logActive) return;
    // Ghi params vào cột NOTE dưới dạng chuỗi key=value
    string params = StringFormat(
        "v=1.10 Sym=%s TF=%s Magic=%d "
        "SessStart=%d SessEnd=%d ServerGMT=%d ForceEOD=%s "
        "Risk=%.1f MaxDailyLoss=%.1f MaxConsecLoss=%d MaxSpread=%.0f "
        "SL=%.0f TP=%.0f BE=%s BEAct=%.0f BELck=%.0f "
        "Trail=%s TrlStart=%.0f TrlStep=%.0f "
        "FastEMA=%d SlowEMA=%d EMABuf=%.0f "
        "RSI=%d BuyRSI=%.0f-%.0f SellRSI=%.0f-%.0f",
        _Symbol, TFName(InpTF), InpMagic,
        InpSessionStart, InpSessionEnd, InpServerGMT, IB(InpForceCloseEOD),
        InpRiskPercent, InpMaxDailyLoss, InpMaxConsecLoss, InpMaxSpread,
        InpStopLoss, InpTakeProfit, IB(InpUseBreakEven), InpBEActivate, InpBELock,
        IB(InpUseTrailing), InpTrailingStart, InpTrailingStep,
        InpFastEMA, InpSlowEMA, InpEMABuffer,
        InpRSIPeriod, InpRSIBuyMin, InpRSIBuyMax, InpRSISellMin, InpRSISellMax
    );

    // EVENT;SERVER_TIME;BAR_TIME;MODE;TICKET;DIR;LOTS;OPEN_PRICE;SL;TP;NEW_SL;CLOSE_PRICE;
    // EMA21;EMA50;RSI;SPREAD;PROFIT;SWAP;COMMISSION;NET_PROFIT;BALANCE;EQUITY;
    // OPEN_TIME;DURATION_MIN;REASON;NOTE
    string row = "INIT" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP  // SERVER_TIME
               + LOG_SEP                       // BAR_TIME (n/a)
               + LogMode() + LOG_SEP           // MODE
               + LOG_SEP LOG_SEP LOG_SEP       // TICKET DIR LOTS
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP  // OPEN_PRICE SL TP NEW_SL CLOSE_PRICE
               + LOG_SEP LOG_SEP LOG_SEP       // EMA21 EMA50 RSI
               + LOG_SEP                       // SPREAD
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP  // PROFIT SWAP COMMISSION NET_PROFIT
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP  // BALANCE
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP  // EQUITY
               + LOG_SEP LOG_SEP LOG_SEP       // OPEN_TIME DURATION_MIN REASON
               + params;                       // NOTE
    LogRow(row);
}

//--- EVENT: DAY_START
void Log_DayStart(datetime day, double balance)
{
    if(!g_logActive) return;
    string row = "DAY_START" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP
               + TS(day) + LOG_SEP  // BAR_TIME = ngày bắt đầu
               + LogMode() + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + F2(balance) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY)) + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + StringFormat("DayStart balance=%.2f", balance);
    LogRow(row);
}

//--- EVENT: BAR_ANALYSIS – ghi đầy đủ điều kiện của bar vừa đóng
//    signal: 1=BUY, -1=SELL, 0=no signal
//    Gọi SAU khi GetSignal() đã fill g_bufFast/Slow/RSI
void Log_BarAnalysis(datetime barTime, int signal,
                     bool buyTrend, bool buyPullback, bool buyBounce, bool buyRSI, bool buyStrength,
                     bool sellTrend, bool sellPullback, bool sellBounce, bool sellRSI, bool sellStrength)
{
    if(!g_logActive) return;

    int    spread  = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
    double ema21   = g_bufFast[1];
    double ema50   = g_bufSlow[1];
    double rsi     = g_bufRSI[1];
    double high1   = iHigh(_Symbol, InpTF, 1);
    double low1    = iLow(_Symbol,  InpTF, 1);
    double close1  = iClose(_Symbol, InpTF, 1);
    double open1   = iOpen(_Symbol,  InpTF, 1);

    string sigStr  = (signal == 1) ? "BUY" : (signal == -1) ? "SELL" : "NONE";
    string note    = StringFormat(
        "sig=%s H=%.5f L=%.5f O=%.5f C=%.5f "
        "BUY[trend=%s pull=%s bounce=%s rsi=%s str=%s] "
        "SELL[trend=%s pull=%s bounce=%s rsi=%s str=%s]",
        sigStr, high1, low1, open1, close1,
        IB(buyTrend),  IB(buyPullback),  IB(buyBounce),  IB(buyRSI),  IB(buyStrength),
        IB(sellTrend), IB(sellPullback), IB(sellBounce), IB(sellRSI), IB(sellStrength)
    );

    string row = "BAR_ANALYSIS" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP   // SERVER_TIME
               + TS(barTime) + LOG_SEP          // BAR_TIME ← key field cho auto backtest
               + LogMode() + LOG_SEP
               + LOG_SEP                        // TICKET
               + sigStr + LOG_SEP               // DIR = tín hiệu
               + LOG_SEP                        // LOTS
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP  // OPEN_PRICE SL TP NEW_SL CLOSE_PRICE
               + FP(ema21) + LOG_SEP            // EMA21
               + FP(ema50) + LOG_SEP            // EMA50
               + F4(rsi)   + LOG_SEP            // RSI
               + IntegerToString(spread) + LOG_SEP  // SPREAD
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP    // PROFIT SWAP COMMISSION NET_PROFIT
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP        // OPEN_TIME DURATION_MIN REASON
               + note;
    LogRow(row);
}

//--- EVENT: SKIP – signal bị filter chặn
void Log_Skip(datetime barTime, int signal, string reason)
{
    if(!g_logActive) return;
    int    spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
    string sigStr = (signal == 1) ? "BUY" : (signal == -1) ? "SELL" : "NONE";

    string row = "SKIP" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP
               + TS(barTime) + LOG_SEP          // BAR_TIME ← tham chiếu bar
               + LogMode() + LOG_SEP
               + LOG_SEP                        // TICKET
               + sigStr + LOG_SEP               // DIR
               + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + FP(g_bufFast[1]) + LOG_SEP     // EMA21
               + FP(g_bufSlow[1]) + LOG_SEP     // EMA50
               + F4(g_bufRSI[1])  + LOG_SEP     // RSI
               + IntegerToString(spread) + LOG_SEP  // SPREAD
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP
               + LOG_SEP LOG_SEP
               + reason + LOG_SEP               // REASON
               + "";
    LogRow(row);
}

//--- EVENT: OPEN – ghi ngay sau khi lệnh mở thành công
//    Cũng lưu vào g_open* globals cho CLOSE event sau này
void Log_Open(datetime barTime, ulong ticket, int dir,
              double lots, double price, double sl, double tp)
{
    if(!g_logActive) return;
    int    spread  = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
    double ema21   = g_bufFast[1];
    double ema50   = g_bufSlow[1];
    double rsi     = g_bufRSI[1];
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity  = AccountInfoDouble(ACCOUNT_EQUITY);

    // Lưu context cho CLOSE event
    g_openTicket = ticket;
    g_openTime   = TimeCurrent();
    g_openPrice  = price;
    g_openDir    = dir;
    g_openLots   = lots;
    g_openSL     = sl;
    g_openTP     = tp;
    g_openSpread = spread;
    g_openEMA21  = ema21;
    g_openEMA50  = ema50;
    g_openRSI    = rsi;

    string dirStr = (dir == 1) ? "BUY" : "SELL";
    string row = "OPEN" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP    // SERVER_TIME ← thời điểm mở lệnh
               + TS(barTime) + LOG_SEP           // BAR_TIME ← bar kích hoạt signal
               + LogMode() + LOG_SEP
               + IntegerToString((long)ticket) + LOG_SEP  // TICKET
               + dirStr + LOG_SEP                // DIR
               + StringFormat("%.2f", lots) + LOG_SEP     // LOTS
               + FP(price)  + LOG_SEP            // OPEN_PRICE ← giá vào lệnh thực tế
               + FP(sl)     + LOG_SEP            // SL
               + FP(tp)     + LOG_SEP            // TP
               + LOG_SEP LOG_SEP                 // NEW_SL CLOSE_PRICE
               + FP(ema21)  + LOG_SEP            // EMA21
               + FP(ema50)  + LOG_SEP            // EMA50
               + F4(rsi)    + LOG_SEP            // RSI
               + IntegerToString(spread) + LOG_SEP   // SPREAD
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP      // PROFIT SWAP COMMISSION NET_PROFIT
               + F2(balance) + LOG_SEP
               + F2(equity)  + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP              // OPEN_TIME DURATION_MIN REASON
               + StringFormat("slPts=%.0f tpPts=%.0f balRisk=%.2f",
                              InpStopLoss, InpTakeProfit, balance * InpRiskPercent / 100.0);
    LogRow(row);
}

//--- EVENT: MODIFY_BE / MODIFY_TRAIL
void Log_Modify(string event, ulong ticket, double oldSL, double newSL)
{
    if(!g_logActive) return;
    string row = event + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP
               + LOG_SEP                         // BAR_TIME
               + LogMode() + LOG_SEP
               + IntegerToString((long)ticket) + LOG_SEP
               + LOG_SEP LOG_SEP                 // DIR LOTS
               + FP(g_openPrice) + LOG_SEP       // OPEN_PRICE (context)
               + FP(oldSL) + LOG_SEP             // SL (old)
               + FP(g_openTP) + LOG_SEP          // TP
               + FP(newSL) + LOG_SEP             // NEW_SL ← giá SL mới
               + LOG_SEP                         // CLOSE_PRICE
               + FP(g_openEMA21) + LOG_SEP
               + FP(g_openEMA50) + LOG_SEP
               + F4(g_openRSI)   + LOG_SEP
               + LOG_SEP                         // SPREAD
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP // PROFIT SWAP COMMISSION NET_PROFIT
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP
               + TS(g_openTime) + LOG_SEP        // OPEN_TIME
               + LOG_SEP LOG_SEP                 // DURATION_MIN REASON
               + StringFormat("oldSL=%.5f newSL=%.5f", oldSL, newSL);
    LogRow(row);
}

//--- EVENT: CLOSE – gọi từ OnTradeTransaction khi deal close
void Log_Close(ulong deal, double closePrice,
               double profit, double swap, double commission,
               long dealReason, datetime closeTime)
{
    if(!g_logActive) return;

    // Xác định reason
    string reason;
    switch((int)dealReason)
    {
        case DEAL_REASON_SL:     reason = "SL";     break;
        case DEAL_REASON_TP:     reason = "TP";     break;
        case DEAL_REASON_EXPERT: reason = "EA";     break;
        case DEAL_REASON_CLIENT: reason = "MANUAL"; break;
        case DEAL_REASON_SO:     reason = "STOP_OUT"; break;
        default:                 reason = "OTHER_" + IntegerToString((int)dealReason);
    }

    double net      = profit + swap + commission;
    double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
    int    durMin   = (g_openTime > 0) ? (int)((closeTime - g_openTime) / 60) : 0;
    string dirStr   = (g_openDir == 1) ? "BUY" : "SELL";

    string row = "CLOSE" + LOG_SEP
               + TS(closeTime) + LOG_SEP          // SERVER_TIME = thời điểm đóng lệnh
               + LOG_SEP                           // BAR_TIME (n/a for close)
               + LogMode() + LOG_SEP
               + IntegerToString((long)g_openTicket) + LOG_SEP  // TICKET = position ticket (khớp với OPEN row)
               + dirStr + LOG_SEP                 // DIR
               + StringFormat("%.2f", g_openLots) + LOG_SEP  // LOTS
               + FP(g_openPrice) + LOG_SEP        // OPEN_PRICE
               + FP(g_openSL) + LOG_SEP           // SL (original)
               + FP(g_openTP) + LOG_SEP           // TP (original)
               + LOG_SEP                          // NEW_SL (n/a at close)
               + FP(closePrice) + LOG_SEP         // CLOSE_PRICE ← giá đóng thực tế
               + FP(g_openEMA21) + LOG_SEP        // EMA21 at entry
               + FP(g_openEMA50) + LOG_SEP        // EMA50 at entry
               + F4(g_openRSI)   + LOG_SEP        // RSI at entry
               + StringFormat("%.0f", g_openSpread) + LOG_SEP  // SPREAD at entry
               + F2(profit)     + LOG_SEP         // PROFIT (raw)
               + F2(swap)       + LOG_SEP         // SWAP
               + F2(commission) + LOG_SEP         // COMMISSION
               + F2(net)        + LOG_SEP         // NET_PROFIT ← thực lãi/lỗ
               + F2(balance)    + LOG_SEP         // BALANCE after close
               + F2(equity)     + LOG_SEP         // EQUITY after close
               + TS(g_openTime) + LOG_SEP         // OPEN_TIME
               + IntegerToString(durMin) + LOG_SEP // DURATION_MIN
               + reason + LOG_SEP                  // REASON: SL/TP/EA/MANUAL
               + StringFormat("openSpread=%.0f pipPnL=%.1f",
                              g_openSpread, (g_openLots > 0) ? net / (g_openLots * 10.0) : 0.0);
    LogRow(row);

    // Reset open context
    g_openTicket = 0; g_openTime = 0; g_openDir = 0;
    g_openPrice = g_openSL = g_openTP = g_openLots = 0;
}

//--- EVENT: SESSION_STOP
void Log_SessionStop(int consecCount)
{
    if(!g_logActive) return;
    string row = "SESSION_STOP" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP
               + LOG_SEP + LogMode() + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP
               + LOG_SEP LOG_SEP
               + "CONSEC_LOSS" + LOG_SEP
               + StringFormat("consecutiveLoss=%d limit=%d", consecCount, InpMaxConsecLoss);
    LogRow(row);
}

//--- EVENT: DAY_END
void Log_DayEnd(int wins, int losses, double pnl)
{
    if(!g_logActive) return;
    string row = "DAY_END" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP
               + TS(g_lastDay) + LOG_SEP         // BAR_TIME = ngày kết thúc
               + LogMode() + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP + F2(pnl) + LOG_SEP  // NET_PROFIT = pnl ngày
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + StringFormat("dayWin=%d dayLoss=%d dayPnL=%.2f WR=%.1f%%",
                              wins, losses, pnl,
                              (wins + losses > 0) ? 100.0 * wins / (wins + losses) : 0.0);
    LogRow(row);
}

//--- EVENT: FINAL
void Log_Final()
{
    if(!g_logActive) return;
    double wr = (g_totalWin + g_totalLoss > 0)
                ? 100.0 * g_totalWin / (g_totalWin + g_totalLoss) : 0.0;
    string row = "FINAL" + LOG_SEP
               + TS(TimeCurrent()) + LOG_SEP
               + LOG_SEP + LogMode() + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP + F2(g_totalPnL) + LOG_SEP  // NET_PROFIT total
               + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + LOG_SEP
               + F2(AccountInfoDouble(ACCOUNT_EQUITY))  + LOG_SEP
               + LOG_SEP LOG_SEP LOG_SEP
               + StringFormat("totalWin=%d totalLoss=%d totalPnL=%.2f WR=%.1f%%",
                              g_totalWin, g_totalLoss, g_totalPnL, wr);
    LogRow(row);
}

//+------------------------------------------------------------------+
//| ===== EA CORE FUNCTIONS ========================================= |
//+------------------------------------------------------------------+

int OnInit()
{
    if(_Symbol != "EURUSD" && _Symbol != "EURUSDm")
        PrintFormat("⚠️ Symbol %s – EA thiết kế cho EURUSD/EURUSDm!", _Symbol);

    g_trade.SetExpertMagicNumber(InpMagic);
    g_trade.SetDeviationInPoints(10);
    g_trade.SetTypeFilling(ORDER_FILLING_FOK);

    g_hFastEMA = iMA(_Symbol, InpTF, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
    g_hSlowEMA = iMA(_Symbol, InpTF, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
    g_hRSI     = iRSI(_Symbol, InpTF, InpRSIPeriod, PRICE_CLOSE);

    if(g_hFastEMA == INVALID_HANDLE || g_hSlowEMA == INVALID_HANDLE || g_hRSI == INVALID_HANDLE)
    {
        Print("❌ Lỗi tạo indicator handle!");
        return INIT_FAILED;
    }

    ArraySetAsSeries(g_bufFast, true);
    ArraySetAsSeries(g_bufSlow, true);
    ArraySetAsSeries(g_bufRSI,  true);

    g_dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE);

    // Mở log file và ghi INIT
    LogOpen();
    Log_Init();

    PrintFormat("✅ ScalpingEURUSD v1.10 | Magic=%d | SL=%g pts | TP=%g pts | Risk=%.1f%% | Log=%s",
                InpMagic, InpStopLoss, InpTakeProfit, InpRiskPercent,
                g_logActive ? g_logFile : "OFF");
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    Log_DayEnd(g_dayWin, g_dayLoss, g_dayPnL);  // Đóng ngày cuối
    Log_Final();

    IndicatorRelease(g_hFastEMA);
    IndicatorRelease(g_hSlowEMA);
    IndicatorRelease(g_hRSI);

    if(g_logHandle != INVALID_HANDLE)
    {
        FileClose(g_logHandle);
        g_logHandle = INVALID_HANDLE;
        g_logActive = false;
    }
}

//+------------------------------------------------------------------+
//| Utility                                                          |
//+------------------------------------------------------------------+
datetime VNTime()
{
    return TimeCurrent() + (7 - InpServerGMT) * 3600;
}

bool IsSessionTime()
{
    MqlDateTime dt;
    TimeToStruct(VNTime(), dt);
    return (dt.hour >= InpSessionStart && dt.hour < InpSessionEnd);
}

datetime TodayStart()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    dt.hour = 0; dt.min = 0; dt.sec = 0;
    return StructToTime(dt);
}

bool IsMaxDailyLossReached()
{
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double lost   = g_dayStartBal - equity;
    return (lost >= g_dayStartBal * InpMaxDailyLoss / 100.0);
}

bool IsSpreadOK()
{
    return (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) <= InpMaxSpread;
}

bool HasMyPosition()
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
        if(g_pos.SelectByIndex(i) && g_pos.Symbol() == _Symbol && g_pos.Magic() == InpMagic)
            return true;
    return false;
}

double GetLotSize(double slPts)
{
    double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
    double risk     = balance * InpRiskPercent / 100.0;
    double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSz   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    double ptVal    = tickVal / tickSz * _Point;

    double lot  = risk / (slPts * ptVal);
    double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    lot = MathFloor(lot / step) * step;
    return MathMax(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN),
           MathMin(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), lot));
}

//+------------------------------------------------------------------+
//| Position Management                                              |
//+------------------------------------------------------------------+
void ApplyBreakEven()
{
    if(!InpUseBreakEven) return;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(!g_pos.SelectByIndex(i)) continue;
        if(g_pos.Symbol() != _Symbol || g_pos.Magic() != InpMagic) continue;

        double open  = g_pos.PriceOpen();
        double curSL = g_pos.StopLoss();
        double beAct = InpBEActivate * _Point;
        double beLck = InpBELock * _Point;

        if(g_pos.PositionType() == POSITION_TYPE_BUY)
        {
            double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            double newSL = open + beLck;
            if(bid >= open + beAct && NormalizeDouble(newSL - curSL, _Digits) > 0)
            {
                if(g_trade.PositionModify(g_pos.Ticket(), newSL, g_pos.TakeProfit()))
                    Log_Modify("MODIFY_BE", g_pos.Ticket(), curSL, newSL);
            }
        }
        else
        {
            double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            double newSL = open - beLck;
            if(ask <= open - beAct && (curSL == 0 || NormalizeDouble(curSL - newSL, _Digits) > 0))
            {
                if(g_trade.PositionModify(g_pos.Ticket(), newSL, g_pos.TakeProfit()))
                    Log_Modify("MODIFY_BE", g_pos.Ticket(), curSL, newSL);
            }
        }
    }
}

void ApplyTrailing()
{
    if(!InpUseTrailing) return;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(!g_pos.SelectByIndex(i)) continue;
        if(g_pos.Symbol() != _Symbol || g_pos.Magic() != InpMagic) continue;

        double trail = InpTrailingStep * _Point;
        double start = InpTrailingStart * _Point;
        double open  = g_pos.PriceOpen();
        double curSL = g_pos.StopLoss();

        if(g_pos.PositionType() == POSITION_TYPE_BUY)
        {
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(bid < open + start) continue;
            double newSL = bid - trail;
            if(newSL > curSL + _Point)
            {
                if(g_trade.PositionModify(g_pos.Ticket(), newSL, g_pos.TakeProfit()))
                    Log_Modify("MODIFY_TRAIL", g_pos.Ticket(), curSL, newSL);
            }
        }
        else
        {
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            if(ask > open - start) continue;
            double newSL = ask + trail;
            if(curSL == 0 || newSL < curSL - _Point)
            {
                if(g_trade.PositionModify(g_pos.Ticket(), newSL, g_pos.TakeProfit()))
                    Log_Modify("MODIFY_TRAIL", g_pos.Ticket(), curSL, newSL);
            }
        }
    }
}

void ForceCloseAll(string reason)
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(!g_pos.SelectByIndex(i)) continue;
        if(g_pos.Symbol() != _Symbol || g_pos.Magic() != InpMagic) continue;
        // CLOSE event sẽ được ghi trong OnTradeTransaction với reason = EA
        g_trade.PositionClose(g_pos.Ticket());
        PrintFormat("🔴 Force close: %s | PnL=%.2f", reason, g_pos.Profit());
    }
}

//+------------------------------------------------------------------+
//| Signal Detection – trả về signal VÀ các điều kiện con           |
//+------------------------------------------------------------------+
int GetSignal(bool &buyTrend,  bool &buyPullback,  bool &buyBounce,  bool &buyRSI,  bool &buyStrength,
              bool &sellTrend, bool &sellPullback, bool &sellBounce, bool &sellRSI, bool &sellStrength)
{
    // Init tất cả false
    buyTrend  = buyPullback  = buyBounce  = buyRSI  = buyStrength  = false;
    sellTrend = sellPullback = sellBounce = sellRSI = sellStrength = false;

    if(CopyBuffer(g_hFastEMA, 0, 0, 4, g_bufFast) < 4) return 0;
    if(CopyBuffer(g_hSlowEMA, 0, 0, 4, g_bufSlow) < 4) return 0;
    if(CopyBuffer(g_hRSI,     0, 0, 4, g_bufRSI)  < 4) return 0;

    double ema21_1  = g_bufFast[1], ema50_1 = g_bufSlow[1], rsi_1  = g_bufRSI[1];
    double ema21_2  = g_bufFast[2], ema50_2 = g_bufSlow[2];
    double high_1   = iHigh(_Symbol, InpTF, 1);
    double low_1    = iLow(_Symbol,  InpTF, 1);
    double close_1  = iClose(_Symbol, InpTF, 1);
    double open_1   = iOpen(_Symbol,  InpTF, 1);
    double close_2  = iClose(_Symbol, InpTF, 2);
    double bufPts   = InpEMABuffer * _Point;

    // BUY conditions
    buyTrend    = (ema21_1 > ema50_1) && (ema21_2 > ema50_2);
    buyPullback = (low_1 <= ema21_1 + bufPts);
    buyBounce   = (close_1 > ema21_1) && (close_1 > open_1);
    buyRSI      = (rsi_1 >= InpRSIBuyMin && rsi_1 <= InpRSIBuyMax);
    buyStrength = (close_2 > ema50_2);

    if(buyTrend && buyPullback && buyBounce && buyRSI && buyStrength)
        return 1;

    // SELL conditions
    sellTrend    = (ema21_1 < ema50_1) && (ema21_2 < ema50_2);
    sellPullback = (high_1 >= ema21_1 - bufPts);
    sellBounce   = (close_1 < ema21_1) && (close_1 < open_1);
    sellRSI      = (rsi_1 >= InpRSISellMin && rsi_1 <= InpRSISellMax);
    sellStrength = (close_2 < ema50_2);

    if(sellTrend && sellPullback && sellBounce && sellRSI && sellStrength)
        return -1;

    return 0;
}

//+------------------------------------------------------------------+
//| Open Order                                                       |
//+------------------------------------------------------------------+
void OpenOrder(int dir, datetime barTime)
{
    double sl   = InpStopLoss  * _Point;
    double tp   = InpTakeProfit * _Point;
    double lots = GetLotSize(InpStopLoss);
    string cmt  = InpComment + (dir == 1 ? "_B" : "_S");

    if(dir == 1)
    {
        double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
        if(g_trade.Buy(lots, _Symbol, ask, ask - sl, ask + tp, cmt))
        {
            ulong ticket = g_trade.ResultDeal();
            // Lấy position ticket từ deal
            if(HistoryDealSelect(ticket))
            {
                ulong posTicket = HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
                Log_Open(barTime, posTicket, dir, lots, ask, ask - sl, ask + tp);
            }
            PrintFormat("✅ BUY %.2f lot @ %.5f | SL=%.5f | TP=%.5f | RSI=%.1f",
                        lots, ask, ask - sl, ask + tp, g_bufRSI[1]);
        }
        else
            PrintFormat("❌ BUY failed: %d – %s", g_trade.ResultRetcode(), g_trade.ResultComment());
    }
    else
    {
        double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
        if(g_trade.Sell(lots, _Symbol, bid, bid + sl, bid - tp, cmt))
        {
            ulong ticket = g_trade.ResultDeal();
            if(HistoryDealSelect(ticket))
            {
                ulong posTicket = HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
                Log_Open(barTime, posTicket, dir, lots, bid, bid + sl, bid - tp);
            }
            PrintFormat("✅ SELL %.2f lot @ %.5f | SL=%.5f | TP=%.5f | RSI=%.1f",
                        lots, bid, bid + sl, bid - tp, g_bufRSI[1]);
        }
        else
            PrintFormat("❌ SELL failed: %d – %s", g_trade.ResultRetcode(), g_trade.ResultComment());
    }
}

//+------------------------------------------------------------------+
//| Daily Reset                                                      |
//+------------------------------------------------------------------+
void CheckDayReset()
{
    datetime today = TodayStart();
    if(today == g_lastDay) return;

    if(g_lastDay != 0)
    {
        PrintFormat("📊 Ngày kết thúc | Thắng=%d | Thua=%d | PnL=%.2f",
                    g_dayWin, g_dayLoss, g_dayPnL);
        Log_DayEnd(g_dayWin, g_dayLoss, g_dayPnL);
    }

    g_lastDay        = today;
    g_dayStartBal    = AccountInfoDouble(ACCOUNT_BALANCE);
    g_consecLoss     = 0;
    g_sessionStopped = false;
    g_dayWin         = 0;
    g_dayLoss        = 0;
    g_dayPnL         = 0.0;

    Log_DayStart(today, g_dayStartBal);
}

//+------------------------------------------------------------------+
//| Comment on Chart                                                 |
//+------------------------------------------------------------------+
void UpdateComment()
{
    if((bool)MQLInfoInteger(MQL_TESTER)) return;  // Không cần comment trong tester
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double lost   = g_dayStartBal - equity;
    string status = g_sessionStopped ? "STOPPED" : (IsSessionTime() ? "ACTIVE" : "OFF-HOURS");

    string txt = StringFormat(
        "ScalpingEURUSD v1.10\n"
        "Status: %s | Log: %s\n"
        "Session: %d:00-%d:00 VN\n"
        "Spread: %d pts | Max: %d pts\n"
        "Risk/trade: %.1f%% | Daily Loss: %.2f/%.2f%%\n"
        "ConsecLoss: %d/%d\n"
        "Today W/L: %d/%d | PnL: %.2f\n"
        "Total W/L: %d/%d | PnL: %.2f\n",
        status, g_logActive ? "ON" : "OFF",
        InpSessionStart, InpSessionEnd,
        (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD), (int)InpMaxSpread,
        InpRiskPercent, (g_dayStartBal > 0) ? (lost / g_dayStartBal * 100.0) : 0.0, InpMaxDailyLoss,
        g_consecLoss, InpMaxConsecLoss,
        g_dayWin, g_dayLoss, g_dayPnL,
        g_totalWin, g_totalLoss, g_totalPnL
    );
    Comment(txt);
}

//+------------------------------------------------------------------+
//| OnTick                                                           |
//+------------------------------------------------------------------+
void OnTick()
{
    CheckDayReset();
    UpdateComment();

    // Quản lý lệnh đang mở
    if(HasMyPosition())
    {
        ApplyBreakEven();
        ApplyTrailing();
        if(InpForceCloseEOD && !IsSessionTime())
            ForceCloseAll("Session End");
        return;
    }

    // Bar filter
    datetime curBar = iTime(_Symbol, InpTF, 0);
    if(curBar == g_lastBar) return;
    g_lastBar = curBar;

    // Đọc tín hiệu (fill g_buf* + trả về conditions)
    bool bT, bP, bB, bR, bS, sT, sP, sB, sR, sStr;
    int sig = GetSignal(bT, bP, bB, bR, bS, sT, sP, sB, sR, sStr);

    // Ghi BAR_ANALYSIS cho mọi bar (kể cả không có signal)
    Log_BarAnalysis(iTime(_Symbol, InpTF, 1), sig,
                    bT, bP, bB, bR, bS, sT, sP, sB, sR, sStr);

    if(sig == 0) return;  // Không có signal → dừng

    // Kiểm tra các filter
    if(g_sessionStopped)
    { Log_Skip(iTime(_Symbol, InpTF, 1), sig, "CONSEC_LOSS"); return; }

    if(IsMaxDailyLossReached())
    { Log_Skip(iTime(_Symbol, InpTF, 1), sig, "DAILY_LOSS"); return; }

    if(!IsSessionTime())
    { Log_Skip(iTime(_Symbol, InpTF, 1), sig, "SESSION"); return; }

    if(!IsSpreadOK())
    { Log_Skip(iTime(_Symbol, InpTF, 1), sig, StringFormat("SPREAD_%d", (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD))); return; }

    // Mở lệnh
    OpenOrder(sig, iTime(_Symbol, InpTF, 1));
}

//+------------------------------------------------------------------+
//| OnTradeTransaction                                               |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&    req,
                        const MqlTradeResult&     res)
{
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
    if(!HistoryDealSelect(trans.deal))            return;
    if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagic) return;

    long   entry      = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
    double profit     = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
    double swap       = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
    double commission = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
    double closePrice = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
    long   dealReason = HistoryDealGetInteger(trans.deal, DEAL_REASON);
    datetime dealTime = (datetime)HistoryDealGetInteger(trans.deal, DEAL_TIME);

    if(entry != DEAL_ENTRY_OUT) return;

    double net = profit + swap + commission;
    g_totalPnL += net;
    g_dayPnL   += net;

    // Ghi CLOSE log
    Log_Close(trans.deal, closePrice, profit, swap, commission, dealReason, dealTime);

    if(net >= 0)
    {
        g_totalWin++;
        g_dayWin++;
        g_consecLoss = 0;
        PrintFormat("WIN +%.2f | W=%d L=%d | PnL=%.2f", net, g_totalWin, g_totalLoss, g_totalPnL);
    }
    else
    {
        g_totalLoss++;
        g_dayLoss++;
        g_consecLoss++;
        PrintFormat("LOSS %.2f | ConsecLoss=%d/%d | W=%d L=%d | PnL=%.2f",
                    net, g_consecLoss, InpMaxConsecLoss, g_totalWin, g_totalLoss, g_totalPnL);

        if(g_consecLoss >= InpMaxConsecLoss)
        {
            g_sessionStopped = true;
            Log_SessionStop(g_consecLoss);
            PrintFormat("STOP SESSION: %d consecutive losses", g_consecLoss);
        }
    }
}

//+------------------------------------------------------------------+
//| OnChartEvent – phím tắt                                         |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
    if(id != CHARTEVENT_KEYDOWN) return;

    if(lparam == 82) // R – Reset
    {
        g_sessionStopped = false;
        g_consecLoss = 0;
        Print("Reset: consecLoss=0, session resumed");
    }
    if(lparam == 88) // X – Close all
    {
        ForceCloseAll("Manual X key");
    }
    if(lparam == 73) // I – Info/stats
    {
        PrintFormat("Stats | W=%d | L=%d | PnL=%.2f | ConsecLoss=%d | DayLoss=%.2f%% | Log=%s",
                    g_totalWin, g_totalLoss, g_totalPnL, g_consecLoss,
                    (g_dayStartBal > 0) ? (g_dayStartBal - AccountInfoDouble(ACCOUNT_EQUITY)) / g_dayStartBal * 100.0 : 0.0,
                    g_logActive ? g_logFile : "OFF");
    }
}
//+------------------------------------------------------------------+
