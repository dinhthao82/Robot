//+------------------------------------------------------------------+
//|  GridBot_EUR_USD.mq5                                             |
//|  GridBot v3.0 – Exness Pro EUR/USD                              |
//|  Backtest-optimized: spread filter, GMT+7 session,              |
//|  daily DD guard, profit withdrawal, full statistics             |
//+------------------------------------------------------------------+
#property copyright "GridBot v3.2"
#property version   "3.20"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade        trade;
CPositionInfo posInfo;

//============================================================
//  INPUT PARAMETERS
//============================================================

input group "=== THỜI GIAN GIAO DỊCH (GMT+7) ==="
input int     InpStartHour       = 10;      // Giờ bắt đầu (GMT+7)
input int     InpEndHour         = 19;     // Giờ kết thúc (GMT+7)
input int     InpGMTOffset       = 7;      // Offset: server → GMT+7

input group "=== SPREAD FILTER – Exness Pro ==="
input double  InpMaxSpread       = 10;     // Max spread (points) – 1 pip Pro account

input group "=== CÀI ĐẶT LƯỚI ==="
input double  InpLotSize         = 0.01;   // Lot size cố định
input double  InpProfitPerOrder  = 1.0;    // Lệnh mới nhất lời >= $X → nhồi thêm
input int     InpSlippage        = 10;     // Slippage tối đa (points)
input int     InpMagicBuy        = 202401; // Magic Number BUY
input int     InpMagicSell       = 202402; // Magic Number SELL
input int     InpMinOrdersScale  = 3;      // Từ lệnh nhồi thứ N: yêu cầu ALL lãi

input group "=== ĐÓNG TOÀN BỘ ==="
input double  InpTakeProfitTotal = 10.0;   // TP tổng ($)
input double  InpStopLossTotal   = 5.0;    // SL tổng ($)

input group "=== EARLY EXIT – Chốt khi lãi (on/off) ==="
input bool    InpEarlyExitOn        = true;  // Bật/tắt early exit
input double  InpEarlyProfitThresh  = 5.0;   // Lời 1 hướng > $X → đóng hết
input int     InpMinOrdersForEarly  = 2;     // Kích hoạt khi nhồi >= N lệnh (đã giảm 3→2)

input group "=== ĐÓNG LỆNH NGƯỢC HƯỚNG (on/off) ==="
input bool    InpCloseOppositeOn    = true;  // Bật/tắt đóng ngược hướng
input int     InpMinOrdersForOpp    = 2;     // Kích hoạt khi hướng chủ >= N lệnh (đã giảm 3→2)

input group "=== KIỂM SOÁT LỖ & THỜI GIAN ==="
input double  InpDirStopLoss        = 0.0;   // SL từng chiều ($) — 0=tắt (log: gây -$6/round, xấu hơn tự nhiên)
input double  InpMaxHoldHours       = 4.0;   // Đóng round nếu lỗ & giữ quá X giờ — KHÔNG áp dụng cho depth>=3
input double  InpLockedHedgeLoss    = 2.0;   // Đóng side tệ hơn khi B==S==N và side đó lỗ > $X (0=tắt)

input group "=== BẢO VỆ TÀI KHOẢN ==="
input double  InpDailyDrawdownPct   = 20.0;  // Dừng khi TK giảm X% từ đỉnh ngày
input double  InpMinBalance         = 100.0; // Rút lãi 50% phần > MinBalance cuối phiên
input double  InpLowBalanceStop     = 30.0;  // Dừng bot vĩnh viễn khi TK < $X

input group "=== BÙ VỐN ĐẦU TUẦN (on/off) ==="
input bool    InpWeeklyTopUpOn      = true;   // Bật/tắt bù vốn đầu tuần từ quỹ rút lãi
input double  InpTopUpTarget        = 100.0;  // Bù về mức $X nếu balance thấp hơn

//============================================================
//  BIẾN TOÀN CỤC
//============================================================

// Trạng thái bot
bool     g_InitOrdersOpened   = false;
datetime g_LastBarTime        = 0;
bool     g_BotStopped         = false;   // TK < InpLowBalanceStop → dừng hoàn toàn

// Bảo vệ tài khoản
double   g_DailyHighBalance   = 0.0;
bool     g_DrawdownHit        = false;   // Đã chạm DD ngưỡng ngày hôm nay
bool     g_DayWithdrawalDone  = false;   // Đã thông báo rút lãi hôm nay
int      g_LastDay            = -1;

// P/L đã thực hiện trong vòng hiện tại (từ CloseDirection)
double   g_RoundRealizedPL    = 0.0;

// Thống kê
int      g_TotalRounds        = 0;
int      g_WinRounds          = 0;
double   g_GrossProfit        = 0.0;
double   g_GrossLoss          = 0.0;
int      g_MaxOrdersEver      = 0;
int      g_CurrentRoundOrders = 0;
int      g_ConsecWins         = 0;
int      g_ConsecLosses       = 0;
int      g_MaxConsecWins      = 0;
int      g_MaxConsecLosses    = 0;
double   g_BestRound          = 0.0;
double   g_WorstRound         = 0.0;
int      g_TotalBuyOpened     = 0;
int      g_TotalSellOpened    = 0;
double   g_TotalWithdrawn     = 0.0;  // Tổng lãi đề xuất rút tích lũy (bao gồm đã bù vốn)

// Kiểm soát in thống kê theo tháng
int      g_LastStatMonth      = -1;
int      g_LastStatYear       = -1;

// Bù vốn đầu tuần
int      g_LastTopUpWeek      = -1;

// Thống kê từng tháng (reset mỗi tháng)
int      g_MonthRounds        = 0;
int      g_MonthWins          = 0;
double   g_MonthGrossProfit   = 0.0;
double   g_MonthGrossLoss     = 0.0;
int      g_MonthBuyOpened     = 0;
int      g_MonthSellOpened    = 0;
double   g_MonthWithdrawn     = 0.0;

// Per-round tracking (để phân tích cải tiến logic)
datetime g_RoundStartTime     = 0;
int      g_RoundPeakBuy       = 0;   // Lệnh BUY nhiều nhất trong vòng
int      g_RoundPeakSell      = 0;   // Lệnh SELL nhiều nhất trong vòng
string   g_RoundCloseReason   = "";

//============================================================
//  UTILITY
//============================================================

bool IsSpreadOK()
{
    double spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
    if(spread > InpMaxSpread)
    {
        PrintFormat("Spread %.0f pts > %.0f – bỏ qua", spread, InpMaxSpread);
        return false;
    }
    return true;
}

// Session check dựa trên server time + offset về GMT+7
bool IsSessionActive()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent() + (long)InpGMTOffset * 3600, dt);
    if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;  // Cuối tuần
    return (dt.hour >= InpStartHour && dt.hour < InpEndHour);
}

int CountOrders(int magic)
{
    int count = 0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
        if(posInfo.SelectByIndex(i) && posInfo.Symbol() == _Symbol && posInfo.Magic() == magic)
            count++;
    return count;
}

// Tổng P/L lệnh đang mở (BUY + SELL), bao gồm swap + commission
double GetTotalPL()
{
    double total = 0.0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol() == _Symbol &&
           (posInfo.Magic() == InpMagicBuy || posInfo.Magic() == InpMagicSell))
            total += posInfo.Profit() + posInfo.Swap() + posInfo.Commission();
    }
    return total;
}

double GetDirectionPL(int magic)
{
    double total = 0.0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
        if(posInfo.SelectByIndex(i) && posInfo.Symbol() == _Symbol && posInfo.Magic() == magic)
            total += posInfo.Profit() + posInfo.Swap() + posInfo.Commission();
    return total;
}

// Tất cả lệnh theo magic có đang lãi không?
bool AllOrdersProfitable(int magic)
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol() == _Symbol && posInfo.Magic() == magic)
            if(posInfo.Profit() + posInfo.Swap() + posInfo.Commission() <= 0) return false;
    }
    return true;
}

// P/L của lệnh được mở SAU CÙNG theo magic
double GetLatestOrderProfit(int magic)
{
    datetime latestTime   = 0;
    double   latestProfit = 0.0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol() == _Symbol && posInfo.Magic() == magic)
        {
            if(posInfo.Time() >= latestTime)
            {
                latestTime   = posInfo.Time();
                latestProfit = posInfo.Profit() + posInfo.Swap() + posInfo.Commission();
            }
        }
    }
    return latestProfit;
}

//============================================================
//  ĐÓNG LỆNH
//============================================================

// Đóng một hướng – ghi nhận realized P/L vào vòng hiện tại
void CloseDirection(int magic, string reason)
{
    double dirPL = GetDirectionPL(magic);
    g_RoundRealizedPL += dirPL;  // Cộng dồn vào P/L vòng để tính stats chính xác

    string dir = (magic == InpMagicBuy) ? "BUY" : "SELL";
    PrintFormat("[CLOSE %s] %s | P/L: $%.2f", dir, reason, dirPL);

    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol() == _Symbol && posInfo.Magic() == magic)
            trade.PositionClose(posInfo.Ticket());
    }
}

// Đóng tất cả – kết thúc 1 vòng và cập nhật thống kê
void CloseAllOrders(string reason)
{
    // Tổng P/L = floating hiện tại + đã realized trong vòng này (từ CloseDirection)
    double roundPL = GetTotalPL() + g_RoundRealizedPL;

    // Cập nhật max orders vòng này
    int curTotal = CountOrders(InpMagicBuy) + CountOrders(InpMagicSell);
    int roundMax = MathMax(g_CurrentRoundOrders, curTotal);
    if(roundMax > g_MaxOrdersEver) g_MaxOrdersEver = roundMax;

    // Thống kê vòng (cumulative + monthly)
    g_TotalRounds++;
    g_MonthRounds++;
    if(roundPL > 0.0)
    {
        g_WinRounds++;      g_MonthWins++;
        g_GrossProfit  += roundPL;  g_MonthGrossProfit += roundPL;
        g_ConsecWins++;
        g_ConsecLosses  = 0;
        if(g_ConsecWins  > g_MaxConsecWins)  g_MaxConsecWins  = g_ConsecWins;
        if(roundPL       > g_BestRound)      g_BestRound      = roundPL;
    }
    else
    {
        double absLoss = MathAbs(roundPL);
        g_GrossLoss    += absLoss;  g_MonthGrossLoss += absLoss;
        g_ConsecLosses++;
        g_ConsecWins    = 0;
        if(g_ConsecLosses > g_MaxConsecLosses) g_MaxConsecLosses = g_ConsecLosses;
        if(roundPL        < g_WorstRound)       g_WorstRound      = roundPL;
    }

    PrintFormat("═══ ĐÓNG TẤT CẢ | %s | Vòng #%d | P/L: $%.2f ═══",
               reason, g_TotalRounds, roundPL);

    LogRound(reason, roundPL);  // Log per-round data để phân tích

    CloseDirection(InpMagicBuy,  reason);
    CloseDirection(InpMagicSell, reason);

    // Reset trạng thái vòng
    g_InitOrdersOpened   = false;
    g_RoundRealizedPL    = 0.0;
    g_CurrentRoundOrders = 0;
    g_RoundStartTime     = 0;
    g_RoundPeakBuy       = 0;
    g_RoundPeakSell      = 0;

    Print("=== Reset xong. Chờ vào lệnh lại ===");
}

//============================================================
//  BẢO VỆ TÀI KHOẢN
//============================================================

// Phát hiện ngày mới → reset daily stats; tháng mới → in thống kê
void CheckDayChange()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent() + (long)InpGMTOffset * 3600, dt);
    if(dt.day == g_LastDay) return;

    // Phát hiện tháng mới → ghi CSV + in thống kê tháng vừa qua
    if(g_LastStatMonth != -1 && (dt.mon != g_LastStatMonth || dt.year != g_LastStatYear))
    {
        PrintFormat("═══ TỔNG KẾT THÁNG %04d-%02d ═══", g_LastStatYear, g_LastStatMonth);
        FlushMonth(g_LastStatYear, g_LastStatMonth);
        PrintStats();
    }
    g_LastStatMonth = dt.mon;
    g_LastStatYear  = dt.year;

    g_LastDay            = dt.day;
    g_DailyHighBalance   = AccountInfoDouble(ACCOUNT_BALANCE);
    g_DrawdownHit        = false;
    g_DayWithdrawalDone  = false;
    PrintFormat("═══ NGÀY MỚI %04d-%02d-%02d | Balance reset: $%.2f ═══",
               dt.year, dt.mon, dt.day, g_DailyHighBalance);
}

// Kiểm tra TK < LowBalance → dừng bot hoàn toàn
bool CheckLowBalance()
{
    if(g_BotStopped) return true;
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    if(balance < InpLowBalanceStop)
    {
        g_BotStopped = true;
        string msg = StringFormat("⚠️ LOW BALANCE: $%.2f < $%.2f → Bot dừng hoàn toàn!", balance, InpLowBalanceStop);
        Print(msg);
        Alert(msg);
        if(CountOrders(InpMagicBuy) > 0 || CountOrders(InpMagicSell) > 0)
            CloseAllOrders("Low balance emergency stop");
        return true;
    }
    return false;
}

// Kiểm tra daily drawdown – so sánh balance với đỉnh ngày
bool CheckDailyDrawdown()
{
    if(g_DrawdownHit) return true;

    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    // Cập nhật đỉnh ngày
    if(balance > g_DailyHighBalance) g_DailyHighBalance = balance;

    if(g_DailyHighBalance <= 0.0) return false;
    double dropPct = (g_DailyHighBalance - balance) / g_DailyHighBalance * 100.0;

    if(dropPct >= InpDailyDrawdownPct)
    {
        g_DrawdownHit = true;
        string msg = StringFormat("🛑 DAILY DD: TK giảm %.1f%% ($%.2f → $%.2f) – Dừng ngày hôm nay",
                                 dropPct, g_DailyHighBalance, balance);
        Print(msg);
        Alert(msg);
        if(CountOrders(InpMagicBuy) > 0 || CountOrders(InpMagicSell) > 0)
            CloseAllOrders(StringFormat("Daily drawdown %.1f%%", dropPct));
        return true;
    }
    return false;
}

// Đầu tuần: bù vốn từ quỹ rút lãi nếu balance < InpTopUpTarget
void CheckWeeklyTopUp()
{
    if(!InpWeeklyTopUpOn) return;

    MqlDateTime dt;
    TimeToStruct(TimeCurrent() + (long)InpGMTOffset * 3600, dt);

    // Chỉ xử lý vào Thứ 2 (day_of_week == 1)
    if(dt.day_of_week != 1) return;

    // Tính số tuần trong năm để tránh xử lý nhiều lần trong cùng 1 tuần
    int currentWeek = (int)MathFloor((dt.day_of_year - 1) / 7.0) + dt.year * 53;
    if(currentWeek == g_LastTopUpWeek) return;
    g_LastTopUpWeek = currentWeek;

    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    if(balance >= InpTopUpTarget) return;  // TK đã đủ, không cần bù

    double needed = InpTopUpTarget - balance;

    if(g_TotalWithdrawn <= 0.0)
    {
        string msg = StringFormat(
            "⚠️ ĐẦU TUẦN: Balance $%.2f < $%.2f | Cần bù $%.2f\n"
            "→ Quỹ rút lãi $0.00 – Không đủ để bù vốn!",
            balance, InpTopUpTarget, needed);
        Print(msg);
        Alert(msg);
        return;
    }

    double topUp = MathMin(needed, g_TotalWithdrawn);  // Chỉ bù tối đa quỹ hiện có
    g_TotalWithdrawn -= topUp;                          // Trừ khỏi quỹ rút lãi

    string msg = StringFormat(
        "💵 ĐẦU TUẦN – BÙ VỐN:\n"
        "  Balance hiện tại : $%.2f\n"
        "  Mục tiêu         : $%.2f\n"
        "  Bù vào           : $%.2f\n"
        "  Quỹ rút lãi còn  : $%.2f%s",
        balance, InpTopUpTarget, topUp, g_TotalWithdrawn,
        (topUp < needed)
            ? StringFormat("\n  ⚠️ Thiếu $%.2f (quỹ không đủ bù full)", needed - topUp)
            : "");
    Print(msg);
    Alert(msg);
}

// Cuối phiên: thông báo rút lãi nếu TK > MinBalance
void CheckEndOfDayWithdrawal()
{
    if(g_DayWithdrawalDone) return;
    if(IsSessionActive())    return;

    // Chỉ check vào cuối ngày làm việc
    MqlDateTime dt;
    TimeToStruct(TimeCurrent() + (long)InpGMTOffset * 3600, dt);
    if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
    if(dt.hour < InpEndHour) return;

    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    if(balance > InpMinBalance)
    {
        double excess    = balance - InpMinBalance;
        double withdraw  = excess * 0.5;
        g_TotalWithdrawn  += withdraw;   // Cộng dồn tổng rút lãi
        g_MonthWithdrawn  += withdraw;
        string msg = StringFormat(
            "💰 CUỐI PHIÊN: Số dư $%.2f | Vượt MinBalance $%.2f\n"
            "→ Đề xuất rút lãi: $%.2f (50%% của $%.2f vượt mức)\n"
            "→ Tổng rút lãi tích lũy: $%.2f",
            balance, InpMinBalance, withdraw, excess, g_TotalWithdrawn);
        Print(msg);
        Alert(msg);
    }
    g_DayWithdrawalDone = true;
}

//============================================================
//  MỞ LỆNH KHỞI ĐẦU
//============================================================

void OpenInitialOrders()
{
    if(!IsSpreadOK()) return;

    if(CountOrders(InpMagicBuy) == 0)
    {
        trade.SetExpertMagicNumber(InpMagicBuy);
        double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
        if(trade.Buy(InpLotSize, _Symbol, ask, 0, 0, "GRID-BUY-1"))
        {
            g_TotalBuyOpened++;  g_MonthBuyOpened++;
            PrintFormat("Mở BUY #1 tại %.5f | Spread: %.0f pts",
                       ask, (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
        }
        else
            PrintFormat("Lỗi BUY: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
    }

    if(CountOrders(InpMagicSell) == 0)
    {
        trade.SetExpertMagicNumber(InpMagicSell);
        double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
        if(trade.Sell(InpLotSize, _Symbol, bid, 0, 0, "GRID-SELL-1"))
        {
            g_TotalSellOpened++;  g_MonthSellOpened++;
            PrintFormat("Mở SELL #1 tại %.5f | Spread: %.0f pts",
                       bid, (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
        }
        else
            PrintFormat("Lỗi SELL: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
    }

    g_InitOrdersOpened   = true;
    g_CurrentRoundOrders = 2;
    g_RoundRealizedPL    = 0.0;
    g_RoundStartTime     = TimeCurrent();
    g_RoundPeakBuy       = 1;
    g_RoundPeakSell      = 1;
}

//============================================================
//  GRID LOGIC
//============================================================

// Nhồi lệnh theo một hướng
// Trigger: lệnh SAU CÙNG lời >= InpProfitPerOrder
// Điều kiện an toàn: từ lệnh N trở đi, TẤT CẢ trước phải lãi
void ProcessGridDirection(int magic, string dir)
{
    int orderCount = CountOrders(magic);
    if(orderCount == 0) return;

    // Không nhồi hướng này nếu hướng ngược đang có nhiều hơn (dominant)
    int oppMagic   = (magic == InpMagicBuy) ? InpMagicSell : InpMagicBuy;
    int oppCount   = CountOrders(oppMagic);
    if(oppCount > orderCount) return;  // Hướng ngược đang dominant → bỏ qua

    // Lệnh mới nhất phải lời đủ ngưỡng
    double latestProfit = GetLatestOrderProfit(magic);
    if(latestProfit < InpProfitPerOrder) return;

    // Từ lệnh nhồi thứ N: TẤT CẢ lệnh hướng này phải lãi
    if(orderCount >= InpMinOrdersScale)
    {
        if(!AllOrdersProfitable(magic))
        {
            PrintFormat("[%s] Nhồi #%d: có lệnh chưa lãi → bỏ qua", dir, orderCount + 1);
            return;
        }
    }

    if(!IsSpreadOK()) return;

    trade.SetExpertMagicNumber(magic);
    string tag = dir + "-" + IntegerToString(orderCount + 1);

    if(magic == InpMagicBuy)
    {
        double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
        if(trade.Buy(InpLotSize, _Symbol, ask, 0, 0, "GRID-" + tag))
        {
            g_TotalBuyOpened++;  g_MonthBuyOpened++;
            PrintFormat("[BUY] Nhồi #%d tại %.5f | Latest P/L: $%.2f",
                       orderCount + 1, ask, latestProfit);
        }
        else
            PrintFormat("[BUY] Lỗi nhồi: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
    }
    else
    {
        double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
        if(trade.Sell(InpLotSize, _Symbol, bid, 0, 0, "GRID-" + tag))
        {
            g_TotalSellOpened++;  g_MonthSellOpened++;
            PrintFormat("[SELL] Nhồi #%d tại %.5f | Latest P/L: $%.2f",
                       orderCount + 1, bid, latestProfit);
        }
        else
            PrintFormat("[SELL] Lỗi nhồi: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
    }

    // Cập nhật max orders vòng này + peak per direction
    int total = CountOrders(InpMagicBuy) + CountOrders(InpMagicSell);
    if(total > g_CurrentRoundOrders) g_CurrentRoundOrders = total;
    int nb = CountOrders(InpMagicBuy);  int ns = CountOrders(InpMagicSell);
    if(nb > g_RoundPeakBuy)  g_RoundPeakBuy  = nb;
    if(ns > g_RoundPeakSell) g_RoundPeakSell = ns;
}

// Early exit: đóng toàn bộ khi 1 hướng nhồi đủ + lãi đủ
// Returns true nếu đã đóng
bool ProcessEarlyExit()
{
    if(!InpEarlyExitOn) return false;

    int buyCount  = CountOrders(InpMagicBuy);
    int sellCount = CountOrders(InpMagicSell);

    if(buyCount >= InpMinOrdersForEarly)
    {
        double buyPL = GetDirectionPL(InpMagicBuy);
        if(buyPL > InpEarlyProfitThresh)
        {
            PrintFormat("[EARLY EXIT] BUY %d lệnh lời $%.2f > $%.2f → Chốt lãi",
                       buyCount, buyPL, InpEarlyProfitThresh);
            CloseAllOrders(StringFormat("Early Exit BUY +$%.2f", buyPL));
            return true;
        }
    }

    if(sellCount >= InpMinOrdersForEarly)
    {
        double sellPL = GetDirectionPL(InpMagicSell);
        if(sellPL > InpEarlyProfitThresh)
        {
            PrintFormat("[EARLY EXIT] SELL %d lệnh lời $%.2f > $%.2f → Chốt lãi",
                       sellCount, sellPL, InpEarlyProfitThresh);
            CloseAllOrders(StringFormat("Early Exit SELL +$%.2f", sellPL));
            return true;
        }
    }
    return false;
}

// Đóng lệnh ngược hướng khi hướng chủ nhồi đủ N lệnh
// Fix v3.1→v3.2: dùng counts tươi sau mỗi close để tránh double-close bug
void ProcessCloseOpposite()
{
    if(!InpCloseOppositeOn) return;

    int buyCount  = CountOrders(InpMagicBuy);
    int sellCount = CountOrders(InpMagicSell);

    // BUY dominant (có nhiều hơn SELL) → đóng SELL
    if(buyCount > sellCount && buyCount >= InpMinOrdersForOpp && sellCount > 0)
    {
        PrintFormat("[CLOSE OPP] BUY=%d > SELL=%d → dong SELL", buyCount, sellCount);
        CloseDirection(InpMagicSell, "Close opposite (BUY dominant)");
        sellCount = CountOrders(InpMagicSell);  // refresh
    }

    // SELL dominant (có nhiều hơn BUY) → đóng BUY
    if(sellCount > buyCount && sellCount >= InpMinOrdersForOpp && buyCount > 0)
    {
        PrintFormat("[CLOSE OPP] SELL=%d > BUY=%d → dong BUY", sellCount, buyCount);
        CloseDirection(InpMagicBuy, "Close opposite (SELL dominant)");
    }

    // Locked hedge: B==S==N (symmetric) → đóng side tệ hơn (lỗ nhiều hơn)
    // log cho thấy B=2S=2 = 72 rounds, 0% WR, -$263 total — cần giải phóng
    if(InpLockedHedgeLoss > 0.0)
    {
        buyCount  = CountOrders(InpMagicBuy);
        sellCount = CountOrders(InpMagicSell);
        if(buyCount == sellCount && buyCount >= InpMinOrdersForOpp)
        {
            double buyPL  = GetDirectionPL(InpMagicBuy);
            double sellPL = GetDirectionPL(InpMagicSell);
            // Đóng side nào lỗ quá ngưỡng
            if(buyPL <= -InpLockedHedgeLoss && buyPL < sellPL)
            {
                PrintFormat("[LOCKED HEDGE] B=S=%d | BuyPL=%.2f SellPL=%.2f → dong BUY (worse)",
                           buyCount, buyPL, sellPL);
                CloseDirection(InpMagicBuy, StringFormat("Locked hedge BUY %.2f", buyPL));
            }
            else if(sellPL <= -InpLockedHedgeLoss && sellPL < buyPL)
            {
                PrintFormat("[LOCKED HEDGE] B=S=%d | BuyPL=%.2f SellPL=%.2f → dong SELL (worse)",
                           buyCount, buyPL, sellPL);
                CloseDirection(InpMagicSell, StringFormat("Locked hedge SELL %.2f", sellPL));
            }
        }
    }
}

//============================================================
//  PER-DIRECTION STOP LOSS
//  Đóng 1 chiều khi chiều đó lỗ > InpDirStopLoss
//  (BUY+SELL tự hedge nhau → SL tổng không fire → cần check từng chiều)
//============================================================

void CheckDirectionStop()
{
    if(InpDirStopLoss <= 0.0) return;
    if(!g_InitOrdersOpened) return;

    // BUY side lỗ quá ngưỡng
    if(CountOrders(InpMagicBuy) > 0)
    {
        double buyPL = GetDirectionPL(InpMagicBuy);
        if(buyPL <= -InpDirStopLoss)
        {
            PrintFormat("[DIR SL] BUY P/L $%.2f <= -$%.2f → đóng chiều BUY", buyPL, InpDirStopLoss);
            CloseDirection(InpMagicBuy, StringFormat("Dir SL BUY -$%.2f", MathAbs(buyPL)));
        }
    }

    // SELL side lỗ quá ngưỡng
    if(CountOrders(InpMagicSell) > 0)
    {
        double sellPL = GetDirectionPL(InpMagicSell);
        if(sellPL <= -InpDirStopLoss)
        {
            PrintFormat("[DIR SL] SELL P/L $%.2f <= -$%.2f → đóng chiều SELL", sellPL, InpDirStopLoss);
            CloseDirection(InpMagicSell, StringFormat("Dir SL SELL -$%.2f", MathAbs(sellPL)));
        }
    }

    // Nếu cả hai chiều đã đóng hết → kết thúc round
    if(g_InitOrdersOpened &&
       CountOrders(InpMagicBuy) == 0 && CountOrders(InpMagicSell) == 0)
    {
        // Round đã đóng từng phần qua CloseDirection — finalise stats
        double roundPL = g_RoundRealizedPL;
        int roundMax   = g_CurrentRoundOrders;
        if(roundMax > g_MaxOrdersEver) g_MaxOrdersEver = roundMax;

        g_TotalRounds++;  g_MonthRounds++;
        if(roundPL > 0.0)
        {
            g_WinRounds++; g_MonthWins++;
            g_GrossProfit += roundPL; g_MonthGrossProfit += roundPL;
            g_ConsecWins++;  g_ConsecLosses = 0;
            if(g_ConsecWins > g_MaxConsecWins) g_MaxConsecWins = g_ConsecWins;
            if(roundPL > g_BestRound)          g_BestRound     = roundPL;
        }
        else
        {
            double absL = MathAbs(roundPL);
            g_GrossLoss    += absL; g_MonthGrossLoss += absL;
            g_ConsecLosses++;  g_ConsecWins = 0;
            if(g_ConsecLosses > g_MaxConsecLosses) g_MaxConsecLosses = g_ConsecLosses;
            if(roundPL < g_WorstRound)             g_WorstRound      = roundPL;
        }
        LogRound("Dir SL (both sides)", roundPL);

        g_InitOrdersOpened   = false;
        g_RoundRealizedPL    = 0.0;
        g_CurrentRoundOrders = 0;
        g_RoundStartTime     = 0;
        g_RoundPeakBuy       = 0;
        g_RoundPeakSell      = 0;
        Print("=== Dir SL: cả hai chiều đã đóng. Chờ vào lệnh lại ===");
    }
}

//============================================================
//  MAX HOLD TIME STOP
//  Đóng round nếu đã giữ quá InpMaxHoldHours VÀ đang lỗ
//  Tránh "Hết phiên" loss kéo dài 9h
//============================================================

bool CheckMaxHoldTime()
{
    if(InpMaxHoldHours <= 0.0) return false;
    if(!g_InitOrdersOpened)    return false;
    if(g_RoundStartTime == 0)  return false;

    // Depth >= 3 có WR 64-83% → KHÔNG cắt, để early exit xử lý
    int maxDepth = MathMax(CountOrders(InpMagicBuy), CountOrders(InpMagicSell));
    if(maxDepth >= 3) return false;

    double holdH   = (TimeCurrent() - g_RoundStartTime) / 3600.0;
    if(holdH < InpMaxHoldHours) return false;

    double totalPL = GetTotalPL() + g_RoundRealizedPL;
    if(totalPL >= 0.0) return false;  // Đang lãi → tiếp tục giữ

    PrintFormat("[MAX HOLD] Round %.1fh > %.1fh | depth=%d | P/L $%.2f → đóng tất cả",
               holdH, InpMaxHoldHours, maxDepth, totalPL);
    CloseAllOrders(StringFormat("Max hold %.1fh", holdH));
    return true;
}

//============================================================
//  CHECK ROUND END — finalise stats nếu tất cả lệnh đã bị đóng
//  Fix bug: CloseOpposite đóng cả 2 side nhưng không gọi CloseAllOrders
//  → round stats bị mất, gRoundRealizedPL bị reset silently ở OpenInitialOrders
//============================================================

bool CheckRoundEnd()
{
    if(!g_InitOrdersOpened) return false;
    if(CountOrders(InpMagicBuy) > 0 || CountOrders(InpMagicSell) > 0) return false;

    // Tất cả lệnh đã đóng nhưng round chưa được finalise
    double roundPL = GetTotalPL() + g_RoundRealizedPL;  // floating=0, chỉ còn realized

    int roundMax = g_CurrentRoundOrders;
    if(roundMax > g_MaxOrdersEver) g_MaxOrdersEver = roundMax;

    g_TotalRounds++;  g_MonthRounds++;
    if(roundPL > 0.0)
    {
        g_WinRounds++;   g_MonthWins++;
        g_GrossProfit += roundPL;  g_MonthGrossProfit += roundPL;
        g_ConsecWins++;  g_ConsecLosses = 0;
        if(g_ConsecWins  > g_MaxConsecWins)  g_MaxConsecWins  = g_ConsecWins;
        if(roundPL       > g_BestRound)      g_BestRound      = roundPL;
    }
    else
    {
        double absL = MathAbs(roundPL);
        g_GrossLoss  += absL;  g_MonthGrossLoss += absL;
        g_ConsecLosses++;  g_ConsecWins = 0;
        if(g_ConsecLosses > g_MaxConsecLosses) g_MaxConsecLosses = g_ConsecLosses;
        if(roundPL        < g_WorstRound)      g_WorstRound      = roundPL;
    }

    LogRound("Round ended (all closed)", roundPL);

    g_InitOrdersOpened   = false;
    g_RoundRealizedPL    = 0.0;
    g_CurrentRoundOrders = 0;
    g_RoundStartTime     = 0;
    g_RoundPeakBuy       = 0;
    g_RoundPeakSell      = 0;

    Print("=== CheckRoundEnd: round finalised. Cho vao lenh lai ===");
    return true;
}

//============================================================
//  DATA LOGGING – Print-based, hoạt động cả Tester lẫn Live
//  Lọc log theo prefix [TK_*] → paste vào Excel/CSV
//============================================================

void TK_PrintHeaders()
{
    Print("[TK_MONTH_HDR] month,round,win,loss,winrate,gross_profit,gross_loss,net_profit,expectancy,avg_win,avg_loss,total_buy,total_sell,totalWithdrawn");
    Print("[TK_ROUND_HDR] open_date,open_time,close_reason,peak_buy,peak_sell,duration_h,pl,dominant_dir");
    Print("[TK_FINAL_HDR] total_round,win,loss,winrate,gross_profit,gross_loss,net_profit,profit_factor,expectancy,avg_win,avg_loss,max_orders,streak_w,streak_l,total_buy,total_sell,totalWithdrawn");
}

// Log mỗi tháng – luôn ghi kể cả 0 vòng
void FlushMonth(int year, int month, bool isPartial = false)
{
    int    loseM   = g_MonthRounds - g_MonthWins;
    double winrate = (g_MonthRounds > 0) ? (double)g_MonthWins / g_MonthRounds * 100.0 : 0.0;
    double netP    = g_MonthGrossProfit - g_MonthGrossLoss;
    double avgWin  = (g_MonthWins > 0) ? g_MonthGrossProfit / g_MonthWins : 0.0;
    double avgLoss = (loseM       > 0) ? g_MonthGrossLoss   / loseM       : 0.0;
    double expect  = (avgWin * winrate - avgLoss * (100.0 - winrate)) / 100.0;
    string tag     = StringFormat("%04d-%02d%s", year, month, isPartial ? "(p)" : "");

    Print(StringFormat("[TK_MONTH] %s,%d,%d,%d,%.1f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%d,%.2f",
        tag,
        g_MonthRounds, g_MonthWins, loseM,
        winrate,
        g_MonthGrossProfit, g_MonthGrossLoss, netP,
        expect, avgWin, avgLoss,
        g_MonthBuyOpened, g_MonthSellOpened,
        g_TotalWithdrawn));

    // Reset biến tháng
    g_MonthRounds      = 0;  g_MonthWins        = 0;
    g_MonthGrossProfit = 0.0; g_MonthGrossLoss   = 0.0;
    g_MonthBuyOpened   = 0;  g_MonthSellOpened  = 0;
    g_MonthWithdrawn   = 0.0;
}

// Log mỗi vòng đóng – dữ liệu để cải tiến logic
void LogRound(string reason, double pl)
{
    MqlDateTime dt;
    TimeToStruct(g_RoundStartTime + (long)InpGMTOffset * 3600, dt);
    double durationH = (g_RoundStartTime > 0)
                       ? (TimeCurrent() - g_RoundStartTime) / 3600.0
                       : 0.0;
    string dir = (g_RoundPeakBuy >= g_RoundPeakSell) ? "BUY" : "SELL";

    Print(StringFormat("[TK_ROUND] %04d-%02d-%02d,%02d:%02d,%s,%d,%d,%.1f,%.2f,%s",
        dt.year, dt.mon, dt.day, dt.hour, dt.min,
        reason,
        g_RoundPeakBuy, g_RoundPeakSell,
        durationH, pl, dir));
}

// Log thống kê tổng cuối cùng
void LogFinalStats()
{
    int    lose = g_TotalRounds - g_WinRounds;
    double wr   = (g_TotalRounds > 0) ? (double)g_WinRounds / g_TotalRounds * 100.0 : 0.0;
    double net  = g_GrossProfit - g_GrossLoss;
    double pf   = (g_GrossLoss  > 0.0) ? g_GrossProfit / g_GrossLoss : 0.0;
    double aw   = (g_WinRounds  > 0)   ? g_GrossProfit / g_WinRounds  : 0.0;
    double al   = (lose         > 0)   ? g_GrossLoss   / lose          : 0.0;
    double ex   = (aw * wr - al * (100.0 - wr)) / 100.0;

    Print(StringFormat("[TK_FINAL] %d,%d,%d,%.1f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%d,%d,%d,%d,%.2f",
        g_TotalRounds, g_WinRounds, lose,
        wr, g_GrossProfit, g_GrossLoss, net, pf, ex, aw, al,
        g_MaxOrdersEver, g_MaxConsecWins, g_MaxConsecLosses,
        g_TotalBuyOpened, g_TotalSellOpened,
        g_TotalWithdrawn));
}

//============================================================
//  THỐNG KÊ & UI
//============================================================

void PrintStats()
{
    if(g_TotalRounds == 0)
    {
        Print("═══ THỐNG KÊ: Chưa có vòng nào hoàn thành ═══");
        return;
    }

    int    loseRounds   = g_TotalRounds - g_WinRounds;
    double winrate      = (double)g_WinRounds / g_TotalRounds * 100.0;
    double netProfit    = g_GrossProfit - g_GrossLoss;
    double profitFactor = (g_GrossLoss > 0.0) ? g_GrossProfit / g_GrossLoss : 0.0;
    double avgWin       = (g_WinRounds  > 0)  ? g_GrossProfit / g_WinRounds  : 0.0;
    double avgLoss      = (loseRounds   > 0)  ? g_GrossLoss   / loseRounds   : 0.0;
    double expectancy   = (avgWin * winrate - avgLoss * (100.0 - winrate)) / 100.0;

    Print("╔══════════════════════════════════════════════╗");
    PrintFormat("║  THỐNG KÊ – GridBot v3.0  %s", _Symbol);
    Print("╠══════════════════════════════════════════════╣");
    PrintFormat("║  Tổng vòng    : %d", g_TotalRounds);
    PrintFormat("║  Thắng / Thua : %d / %d", g_WinRounds, loseRounds);
    PrintFormat("║  Winrate      : %.1f%%", winrate);
    Print("║──────────────────────────────────────────────║");
    PrintFormat("║  Gross Profit : +$%.2f", g_GrossProfit);
    PrintFormat("║  Gross Loss   : -$%.2f", g_GrossLoss);
    PrintFormat("║  Net Profit   : $%.2f", netProfit);
    PrintFormat("║  Profit Factor: %.2f", profitFactor);
    PrintFormat("║  Expectancy   : $%.2f / vòng", expectancy);
    Print("║──────────────────────────────────────────────║");
    PrintFormat("║  Avg Win      : $%.2f  |  Avg Loss: $%.2f", avgWin, avgLoss);
    PrintFormat("║  Best Round   : +$%.2f  |  Worst: $%.2f", g_BestRound, g_WorstRound);
    Print("║──────────────────────────────────────────────║");
    PrintFormat("║  Max Orders   : %d lệnh / vòng", g_MaxOrdersEver);
    PrintFormat("║  Max Streak W : %d  |  Max Streak L: %d", g_MaxConsecWins, g_MaxConsecLosses);
    PrintFormat("║  Total BUY    : %d  |  Total SELL: %d", g_TotalBuyOpened, g_TotalSellOpened);
    Print("║──────────────────────────────────────────────║");
    PrintFormat("║  💰 Tổng rút lãi tích lũy : $%.2f", g_TotalWithdrawn);
    Print("╚══════════════════════════════════════════════╝");
}

void UpdateUI()
{
    double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
    double totalPL   = GetTotalPL();
    int    buyCount  = CountOrders(InpMagicBuy);
    int    sellCount = CountOrders(InpMagicSell);
    double spread    = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
    double dailyDD   = (g_DailyHighBalance > 0.0)
                       ? (g_DailyHighBalance - balance) / g_DailyHighBalance * 100.0
                       : 0.0;

    double winrate      = (g_TotalRounds > 0) ? (double)g_WinRounds / g_TotalRounds * 100.0 : 0.0;
    double netProfit    = g_GrossProfit - g_GrossLoss;
    double profitFactor = (g_GrossLoss > 0.0) ? g_GrossProfit / g_GrossLoss : 0.0;

    string botStatus, sessionStatus, ddStatus;

    if(g_BotStopped)           botStatus = "🔴 STOPPED – Low Balance";
    else if(g_DrawdownHit)     botStatus = "🛑 STOPPED – Daily DD";
    else if(IsSessionActive()) botStatus = "🟢 ACTIVE";
    else                       botStatus = "⏸ OUT-OF-SESSION";

    sessionStatus = IsSessionActive()
                    ? StringFormat("%02d:00–%02d:00 GMT+7", InpStartHour, InpEndHour)
                    : "Ngoài phiên";

    ddStatus = g_DrawdownHit
               ? "HIT !"
               : StringFormat("%.1f%% / %.0f%%", dailyDD, InpDailyDrawdownPct);

    Comment(StringFormat(
        "══════════════════════════════════════════\n"
        "  GridBot v3.0  |  %s  |  %s\n"
        "══════════════════════════════════════════\n"
        "  💰 Balance  : $%.2f   Equity: $%.2f\n"
        "  📊 Float P/L: $%.2f\n"
        "  📅 Daily High: $%.2f  |  DD: %s\n"
        "──────────────────────────────────────────\n"
        "  📈 BUY: %d lệnh   📉 SELL: %d lệnh\n"
        "  📡 Spread: %.0f pts  |  Phiên: %s\n"
        "──────────────────────────────────────────\n"
        "  🏆 Vòng: %d  |  Winrate: %.1f%%\n"
        "  💵 Net: $%.2f  |  PF: %.2f\n"
        "  💰 Tổng rút lãi: $%.2f\n"
        "══════════════════════════════════════════",
        _Symbol, botStatus,
        balance, equity,
        totalPL,
        g_DailyHighBalance, ddStatus,
        buyCount, sellCount,
        spread, sessionStatus,
        g_TotalRounds, winrate,
        netProfit, profitFactor,
        g_TotalWithdrawn
    ));
}

//============================================================
//  MAIN EVENT HANDLERS
//============================================================

int OnInit()
{
    trade.SetDeviationInPoints(InpSlippage);
    trade.SetTypeFilling(ORDER_FILLING_IOC);  // Bắt buộc cho Exness hedge mode

    // Khởi tạo daily/monthly stats
    g_DailyHighBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    MqlDateTime dt;
    TimeToStruct(TimeCurrent() + (long)InpGMTOffset * 3600, dt);
    g_LastDay       = dt.day;
    g_LastStatMonth = dt.mon;
    g_LastStatYear  = dt.year;
    g_LastTopUpWeek = (int)MathFloor((dt.day_of_year - 1) / 7.0) + dt.year * 53;

    TK_PrintHeaders();  // In headers vào log để dễ copy-paste thành CSV

    PrintFormat("GridBot v3.2 START | %s | Lot: %.2f | TP: $%.2f | SL: $%.2f",
               _Symbol, InpLotSize, InpTakeProfitTotal, InpStopLossTotal);
    PrintFormat("Phien GMT+7: %02d:00-%02d:00 | Spread max: %.0f pts | Nguong nhoi: $%.2f",
               InpStartHour, InpEndHour, InpMaxSpread, InpProfitPerOrder);
    PrintFormat("Early Exit: %s (min=%d)  |  Close Opp: %s (min=%d)  |  Daily DD: %.0f%%",
               InpEarlyExitOn     ? "BAT" : "TAT", InpMinOrdersForEarly,
               InpCloseOppositeOn ? "BAT" : "TAT", InpMinOrdersForOpp,
               InpDailyDrawdownPct);
    PrintFormat("Dir SL: $%.2f  |  Max Hold: %.1fh  |  MinBalance: $%.2f",
               InpDirStopLoss, InpMaxHoldHours, InpMinBalance);

    return INIT_SUCCEEDED;
}

void OnTick()
{
    // 0. Cập nhật UI mỗi tick
    UpdateUI();

    // 1. TK quá thấp → dừng hoàn toàn
    if(CheckLowBalance()) return;

    // 2. Phát hiện ngày mới → reset daily guards
    CheckDayChange();

    // 2b. Đầu tuần: bù vốn từ quỹ rút lãi nếu cần
    CheckWeeklyTopUp();

    // 3. Daily drawdown → dừng ngày hôm nay
    if(CheckDailyDrawdown()) return;

    // 4. Ngoài phiên → đóng lệnh + check rút lãi
    if(!IsSessionActive())
    {
        if(g_InitOrdersOpened &&
           (CountOrders(InpMagicBuy) > 0 || CountOrders(InpMagicSell) > 0))
            CloseAllOrders("Hết phiên");
        g_InitOrdersOpened = false;
        CheckEndOfDayWithdrawal();
        return;
    }

    // 5. TP / SL tổng
    double totalPL = GetTotalPL();
    if(totalPL >= InpTakeProfitTotal)
    {
        CloseAllOrders(StringFormat("TP tổng +$%.2f", totalPL));
        return;
    }
    if(totalPL <= -InpStopLossTotal)
    {
        CloseAllOrders(StringFormat("SL tổng -$%.2f", MathAbs(totalPL)));
        return;
    }

    // 6. Finalise round nếu tất cả lệnh đã đóng (fix stats-lost bug)
    if(CheckRoundEnd()) return;

    // 7. Mở lệnh khởi đầu (BUY + SELL) nếu chưa có
    if(!g_InitOrdersOpened ||
       (CountOrders(InpMagicBuy) == 0 && CountOrders(InpMagicSell) == 0))
    {
        OpenInitialOrders();
        return;
    }

    // 8. Early exit: chốt lãi sớm khi đủ điều kiện
    if(ProcessEarlyExit()) return;

    // 9. Per-direction stop loss (nếu bật — default OFF)
    CheckDirectionStop();
    if(!g_InitOrdersOpened) return;

    // 10. Max hold time (không áp dụng depth>=3 — chúng có WR 64-83%)
    if(CheckMaxHoldTime()) return;

    // 11. Đóng hướng ngược / locked hedge
    ProcessCloseOpposite();
    if(CheckRoundEnd()) return;  // CloseOpposite có thể đóng cả 2 side

    // 12. Nhồi lệnh – mỗi bar M1 1 lần (tránh spam nhiều lệnh trên cùng tick)
    datetime currentBar = iTime(_Symbol, PERIOD_M1, 0);
    if(currentBar == g_LastBarTime) return;
    g_LastBarTime = currentBar;

    ProcessGridDirection(InpMagicBuy,  "BUY");
    ProcessGridDirection(InpMagicSell, "SELL");
}

void OnDeinit(const int reason)
{
    Comment("");  // Xóa UI
    PrintFormat("GridBot tắt | Reason: %d", reason);

    int openBuy  = CountOrders(InpMagicBuy);
    int openSell = CountOrders(InpMagicSell);
    if(openBuy > 0 || openSell > 0)
        PrintFormat("Còn %d BUY + %d SELL đang mở | Unrealized: $%.2f",
                   openBuy, openSell, GetTotalPL());

    // Flush tháng hiện tại (partial)
    if(g_LastStatMonth != -1)
        FlushMonth(g_LastStatYear, g_LastStatMonth, true);

    LogFinalStats();
    PrintStats();
}

// Gọi sau mỗi lần chạy backtest/optimization – trả về giá trị fitness cho optimizer
double OnTester()
{
    double netProfit    = g_GrossProfit - g_GrossLoss;
    double profitFactor = (g_GrossLoss > 0.0) ? g_GrossProfit / g_GrossLoss : 0.0;
    double winrate      = (g_TotalRounds > 0) ? (double)g_WinRounds / g_TotalRounds * 100.0 : 0.0;

    // Fitness = Net Profit × WinRate% × ProfitFactor (phạt nặng khi PF < 1)
    // Trả về 0 nếu không có vòng nào hoặc lỗ
    if(g_TotalRounds == 0 || netProfit <= 0.0) return 0.0;
    return netProfit * (winrate / 100.0) * profitFactor;
}
