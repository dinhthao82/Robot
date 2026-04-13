//+------------------------------------------------------------------+
//|                                           NewBotGold.mq5         |
//|             BUY-Only Martingale Grid — XAUUSD                    |
//|             AutoProject V2 | v1.10                               |
//+------------------------------------------------------------------+
#property copyright "AutoProject V2"
#property version   "1.10"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>


//=== LOT ===
input group "=== LOT ==="
input bool   AutoLot         = true;     // Scale lot với balance (compounding)
input double BalancePer      = 1000.0;   // Mỗi $X → 0.01 lot (e.g. $1000→0.01, $5000→0.05)
input double MinLot          = 0.01;     // Lot tối thiểu
input double MaxLot          = 2.00;     // Lot tối đa (safety cap)

//=== GRID STRATEGY ===
input group "=== GRID STRATEGY ==="
input double GridStep        = 1.0;      // Giá giảm X USD → thêm BUY tiếp theo
input int    MaxPositions    = 10;       // Tối đa bao nhiêu BUY / cycle
input double TakeProfit_Pct  = 30.0;    // Đóng tất cả khi floating >= X% balance

//=== SPREAD ===
input group "=== SPREAD ==="
input int    MaxSpread_Pts   = 300;      // Max spread (points) cho phép mở lệnh

//=== TIME WINDOW (GMT+7) ===
input group "=== TIME WINDOW (GMT+7) ==="
input int    StartHour_GMT7  = 8;
input int    StartMin_GMT7   = 0;
input int    EndHour_GMT7    = 23;
input int    EndMin_GMT7     = 0;
input int    ServerGMT       = 3;        // Exness GMT+3

//=== RISK MANAGEMENT ===
input group "=== RISK MANAGEMENT ==="
input double MaxDDPct        = 20.0;    // Max daily drawdown % → dừng hôm nay
input double StopBotPct      = 50.0;    // Dừng vĩnh viễn nếu balance < X% vốn ban đầu

//=== MONEY MANAGEMENT ===
input group "=== MONEY MANAGEMENT ==="
input double KeepPrincipal   = 100.0;   // Vốn gốc giữ lại (USD / tuần)
input double WithdrawRatio   = 0.50;    // Rút X% lãi hàng ngày (Mon–Thu)
input bool   EnableWithdraw  = true;    // Bật rút lãi hàng ngày (toggle key W)
input bool   EnableReplenish = true;    // Bật bù vốn thứ Hai (toggle key R)
input bool   EnableMonthlyRpt= true;    // Báo cáo tháng tự động

//=== LOGGING ===
input group "=== LOGGING ==="
input bool   VerboseLog      = true;

//------------------------------------------------------------------
// Globals — Trading
CTrade        trade;
CPositionInfo pos;

int      gMagic         = 20240201;
string   gComment       = "NBG";

double   gStartBalance  = 0.0;     // Balance lúc init (StopBotPct guard)
double   gDayHighEquity = 0.0;
bool     gTodayStopped  = false;
bool     gBotHalted     = false;
datetime gLastCloseTime = 0;
double   gCycleLot      = 0.0;    // Lot khóa lúc mở cycle (tất cả add dùng cùng lot)

//------------------------------------------------------------------
// Globals — Money Management
double   gWithdrawFund      = 0.0;   // Quỹ tích lũy từ rút lãi
double   gTotalWithdrawn    = 0.0;
double   gTotalReplenished  = 0.0;
double   gMonthlyWithdrawn  = 0.0;
double   gMonthlyReplenished= 0.0;
bool     gFundEverGtPrin    = false; // Quỹ đã vượt principal (Case B)
datetime gLastWithdrawDay   = 0;     // Ngày rút lãi cuối
datetime gLastWeeklyClose   = 0;     // Lần cuối rút toàn bộ lãi cuối tuần (Fri)
datetime gLastReplenishWeek = 0;     // Lần cuối bù vốn (Mon)

// Runtime toggles (key W / R)
bool     gWithdrawOn  = true;
bool     gReplenishOn = true;

//------------------------------------------------------------------
// Globals — Stats (All-time)
int      gTotalCycles   = 0;
int      gWinCycles     = 0;
int      gLossCycles    = 0;
double   gTotalPnl      = 0.0;     // Realized PnL tích lũy (cập nhật qua OnTrade)
ulong    gLastDealTicket= 0;

// Stats — Monthly (reset đầu tháng)
int      gMthCycles     = 0;
int      gMthWin        = 0;
int      gMthLoss       = 0;
double   gMthPnl        = 0.0;
double   gMthMaxDD      = 0.0;
double   gMthHighEq     = 0.0;
double   gMthWithdrawn  = 0.0;
double   gMthReplenished= 0.0;
int      gCurMonth      = 0;
int      gCurYear       = 0;

// Stats — Weekly (reset cuối tuần)
int      gWeekNum       = 1;     // Tuần thứ mấy trong tháng
int      gWeekCycles    = 0;
int      gWeekWin       = 0;
int      gWeekLoss      = 0;
double   gWeekPnl       = 0.0;

bool     gIsTester      = false;   // true khi chạy trong Strategy Tester

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   gIsTester = MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION);

   trade.SetExpertMagicNumber(gMagic);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   trade.SetAsyncMode(false);

   gStartBalance  = AccountInfoDouble(ACCOUNT_BALANCE);
   gDayHighEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   gMthHighEq     = gDayHighEquity;

   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   gCurMonth = dt.mon; gCurYear = dt.year;

   LoadState();

   gWithdrawOn  = EnableWithdraw;
   gReplenishOn = EnableReplenish;

   // Khôi phục cycle lot nếu có vị thế đang mở (restart giữa chừng)
   int cnt = CountBuyPositions();
   if(cnt > 0)
   {
      gCycleLot = GetFirstOpenLot();
      Print(StringFormat("[NBG] Resumed mid-cycle: %d positions | lot=%.2f", cnt, gCycleLot));
   }

   FolderCreate("NBG", FILE_COMMON);
   EventSetTimer(60);

   Print(StringFormat("[NBG] Init OK | Balance=%.2f | Principal=%.2f | Fund=%.2f | GridStep=%.2f | TP=%.0f%%",
         gStartBalance, KeepPrincipal, gWithdrawFund, GridStep, TakeProfit_Pct));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   // Ghi final stats — quan trọng trong Tester (không có OnTimer sau khi test xong)
   WriteFinalStatsReport();
   if(!gIsTester) SaveState();
   Print(StringFormat("[NBG] Deinit reason=%d | Cycles=%d | TotalPnl=%+.2f | Fund=%.2f",
         reason, gTotalCycles, gTotalPnl, gWithdrawFund));
}

//+------------------------------------------------------------------+
//| OnTester — trả về equity cuối test, ghi summary (tester only)   |
//+------------------------------------------------------------------+
double OnTester()
{
   WriteFinalStatsReport();
   return AccountInfoDouble(ACCOUNT_EQUITY);
}

//+------------------------------------------------------------------+
//| Tick                                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if(gBotHalted) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);

   if(equity > gDayHighEquity) gDayHighEquity = equity;
   if(equity > gMthHighEq)     gMthHighEq     = equity;

   // Monthly max DD
   double mthDD = gMthHighEq - equity;
   if(mthDD > gMthMaxDD) gMthMaxDD = mthDD;

   CheckNewDay();
   CheckNewMonth();

   // Permanent halt
   if(balance < gStartBalance * StopBotPct / 100.0)
   {
      CloseAll("HALT: balance < " + DoubleToString(StopBotPct, 0) + "% of start");
      gBotHalted = true;
      Alert(StringFormat("[NewBotGold] HALTED — balance %.2f below %.0f%% of start %.2f",
                         balance, StopBotPct, gStartBalance));
      return;
   }

   // Daily drawdown guard
   if(!gTodayStopped)
   {
      double ddPct = (gDayHighEquity > 0)
                     ? (gDayHighEquity - equity) / gDayHighEquity * 100.0 : 0.0;
      if(ddPct >= MaxDDPct)
      {
         gTodayStopped = true;
         CloseAll(StringFormat("DD STOP: %.2f%%", ddPct));
         return;
      }
   }
   if(gTodayStopped) return;

   // TP check chạy mọi tick kể cả ngoài giờ
   CheckTakeProfit();

   // Chỉ mở lệnh mới trong giờ giao dịch
   if(!InTradingWindow()) return;

   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > MaxSpread_Pts)
   {
      if(VerboseLog)
         Print(StringFormat("[NBG] Spread=%d > max=%d — skip", spread, MaxSpread_Pts));
      return;
   }

   if(gLastCloseTime > 0 && TimeCurrent() - gLastCloseTime < 10)
      return;

   RunGrid();
}

//+------------------------------------------------------------------+
//| Timer — money mgmt + hourly stats                                |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(gBotHalted) return;

   if(gWithdrawOn)   CheckDailyWithdraw();
   if(gWithdrawOn)   CheckWeeklyWithdraw();   // Friday: rút toàn bộ lãi
   if(gReplenishOn)  CheckWeeklyReplenish();  // Monday: bù vốn

   static datetime lastStatTime = 0;
   if(TimeCurrent() - lastStatTime >= 3600)
   {
      LogPerformance();
      lastStatTime = TimeCurrent();
   }
   SaveState();
}

//+------------------------------------------------------------------+
//| OnTrade — capture realized PnL từ từng deal đóng                 |
//+------------------------------------------------------------------+
void OnTrade()
{
   if(!HistorySelect(TimeCurrent() - 120, TimeCurrent() + 1)) return;
   int total = HistoryDealsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0 || ticket <= gLastDealTicket) continue;
      if((ulong)HistoryDealGetInteger(ticket, DEAL_MAGIC) != (ulong)gMagic) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol)              continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT)       continue;

      gLastDealTicket = ticket;
      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT)
                    + HistoryDealGetDouble(ticket, DEAL_SWAP)
                    + HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      gTotalPnl += profit;
      gMthPnl   += profit;
   }
}

//+------------------------------------------------------------------+
//| Key W / R — toggle withdraw / replenish on chart                 |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_KEYDOWN) return;
   if(lparam == 87)   // W
   {
      gWithdrawOn = !gWithdrawOn;
      Print(StringFormat("[NBG] Withdraw toggled: %s", gWithdrawOn?"ON":"OFF"));
      Alert(StringFormat("[NewBotGold] Withdraw: %s", gWithdrawOn?"ON":"OFF"));
   }
   else if(lparam == 82)   // R
   {
      gReplenishOn = !gReplenishOn;
      Print(StringFormat("[NBG] Replenish toggled: %s", gReplenishOn?"ON":"OFF"));
      Alert(StringFormat("[NewBotGold] Replenish: %s", gReplenishOn?"ON":"OFF"));
   }
}

//+------------------------------------------------------------------+
//| TAKE PROFIT — đóng tất cả khi floating >= TakeProfit_Pct        |
//+------------------------------------------------------------------+
void CheckTakeProfit()
{
   int cnt = CountBuyPositions();
   if(cnt == 0) return;

   double floating = GetTotalFloatingPnl();
   if(floating <= 0.0) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double target  = balance * TakeProfit_Pct / 100.0;

   if(floating >= target)
   {
      Print(StringFormat("[NBG] TP HIT | Float=%+.2f >= Target=%.2f (%.0f%% × %.2f) | Positions=%d",
            floating, target, TakeProfit_Pct, balance, cnt));
      CloseAll("TP HIT");
      gTotalCycles++; gMthCycles++; gWeekCycles++;
      gWinCycles++;   gMthWin++;   gWeekWin++;
      gWeekPnl += floating;
      LogCycleSummary(floating, true);
   }
}

//+------------------------------------------------------------------+
//| GRID LOGIC                                                        |
//| - Không có vị thế: mở BUY đầu tiên                              |
//| - Có vị thế: thêm BUY khi giá giảm >= GridStep từ lowest open   |
//+------------------------------------------------------------------+
void RunGrid()
{
   int    cnt = CountBuyPositions();
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(cnt == 0)
   {
      gCycleLot = CalcLot();
      if(VerboseLog)
         Print(StringFormat("[NBG] NEW CYCLE | Lot=%.2f | Bid=%.3f", gCycleLot, bid));
      OpenBuy(1);
      return;
   }

   if(cnt >= MaxPositions)
   {
      if(VerboseLog)
         Print(StringFormat("[NBG] MAX POS: %d/%d", cnt, MaxPositions));
      return;
   }

   double lowestOpen = GetLowestOpenPrice();
   if(lowestOpen <= 0.0) return;

   if(bid <= lowestOpen - GridStep)
   {
      if(VerboseLog)
         Print(StringFormat("[NBG] GRID ADD #%d | bid=%.3f | lowest=%.3f | drop=%.3f",
               cnt + 1, bid, lowestOpen, lowestOpen - bid));
      OpenBuy(cnt + 1);
   }
}

//+------------------------------------------------------------------+
//| Open BUY                                                         |
//+------------------------------------------------------------------+
void OpenBuy(int posNum)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   string cmt = StringFormat("%s_B%d", gComment, posNum);
   bool ok = trade.Buy(gCycleLot, _Symbol, ask, 0, 0, cmt);
   if(ok)
      Print(StringFormat("[NBG] BUY #%d | Lot=%.2f | Ask=%.3f | Spread=%d",
            posNum, gCycleLot, ask, (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   else
   {
      Print(StringFormat("[NBG] BUY FAILED #%d | err=%d | %s",
            posNum, GetLastError(), trade.ResultComment()));
      gLastCloseTime = TimeCurrent(); // cooldown sau lỗi
   }
}

//+------------------------------------------------------------------+
//| Close All — retry 5 lần                                          |
//+------------------------------------------------------------------+
void CloseAll(string reason)
{
   Print("[NBG] CloseAll: " + reason);
   for(int attempt = 1; attempt <= 5; attempt++)
   {
      ulong tickets[]; int n = 0;
      for(int i = PositionsTotal()-1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i)) continue;
         if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
         ArrayResize(tickets, n + 1); tickets[n++] = pos.Ticket();
      }
      if(n == 0) break;
      for(int i = 0; i < n; i++)
         if(pos.SelectByTicket(tickets[i]))
            if(pos.StopLoss() != 0 || pos.TakeProfit() != 0)
               trade.PositionModify(tickets[i], 0, 0);
      for(int i = 0; i < n; i++)
         trade.PositionClose(tickets[i]);
      if(attempt < 5) Sleep(200);
   }
   gCycleLot      = 0.0;
   gLastCloseTime = TimeCurrent();

   int rem = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() == gMagic && pos.Symbol() == _Symbol) rem++;
   }
   if(rem > 0) Print(StringFormat("[NBG] CloseAll WARN: %d still open", rem));
   else        Print("[NBG] CloseAll OK");
}

//+------------------------------------------------------------------+
//| RÚT LÃI HÀNG NGÀY (Mon–Thu, sau phiên)                          |
//| Điều kiện: balance > KeepPrincipal, không có lệnh đang mở       |
//| Hành động: rút WithdrawRatio × (balance − principal) vào quỹ    |
//+------------------------------------------------------------------+
void CheckDailyWithdraw()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);

   // Chỉ chạy Mon–Thu (Fri dùng CheckWeeklyWithdraw)
   if(dt.day_of_week < 1 || dt.day_of_week > 4) return;

   // Chỉ trong 5 phút sau khi phiên kết thúc
   int localH = LocalHour(dt);
   int now    = localH * 60 + dt.min;
   int end    = EndHour_GMT7 * 60 + EndMin_GMT7;
   if(now < end || now > end + 5) return;

   // Chỉ 1 lần / ngày
   MqlDateTime lw; TimeToStruct(gLastWithdrawDay, lw);
   if(lw.day == dt.day && lw.mon == dt.mon && lw.year == dt.year) return;

   // Không rút khi đang có lệnh mở
   if(CountBuyPositions() > 0) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= KeepPrincipal) return;

   double profit = balance - KeepPrincipal;
   double amount = profit * WithdrawRatio;
   if(amount < 0.01) return;

   gWithdrawFund      += amount;
   gTotalWithdrawn    += amount;
   gMonthlyWithdrawn  += amount;
   gMthWithdrawn      += amount;
   gLastWithdrawDay    = TimeCurrent();
   if(gWithdrawFund > KeepPrincipal) gFundEverGtPrin = true;

   Print(StringFormat("[NBG] DAILY WITHDRAW +%.2f | Balance=%.2f | Principal=%.2f | Fund=%.2f",
         amount, balance, KeepPrincipal, gWithdrawFund));
   Alert(StringFormat("[NewBotGold] RÚT LÃI NGÀY $%.2f (balance %.2f → %.2f)",
         amount, balance, balance - amount));
   SaveState();
}

//+------------------------------------------------------------------+
//| RÚT TOÀN BỘ LÃI CUỐI TUẦN (Friday sau phiên)                    |
//| Hành động: rút tất cả (balance − principal) → balance = $100    |
//+------------------------------------------------------------------+
void CheckWeeklyWithdraw()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week != 5) return;   // Friday = 5

   int localH = LocalHour(dt);
   int now    = localH * 60 + dt.min;
   int end    = EndHour_GMT7 * 60 + EndMin_GMT7;
   if(now < end || now > end + 5) return;

   // Chỉ 1 lần / tuần
   if(TimeCurrent() - gLastWeeklyClose < 6 * 86400) return;

   // Không rút khi đang có lệnh mở
   if(CountBuyPositions() > 0) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= KeepPrincipal) return;

   double amount = balance - KeepPrincipal;  // rút TẤT CẢ lãi
   if(amount < 0.01) return;

   gWithdrawFund      += amount;
   gTotalWithdrawn    += amount;
   gMonthlyWithdrawn  += amount;
   gMthWithdrawn      += amount;
   gLastWeeklyClose    = TimeCurrent();
   if(gWithdrawFund > KeepPrincipal) gFundEverGtPrin = true;

   Print(StringFormat("[NBG] WEEKLY WITHDRAW +%.2f | Balance %.2f → %.2f | Fund=%.2f",
         amount, balance, KeepPrincipal, gWithdrawFund));
   Alert(StringFormat("[NewBotGold] RÚT CUỐI TUẦN $%.2f — Balance về $%.2f",
         amount, KeepPrincipal));
   LogWeeklySummary();
   SaveState();
}

//+------------------------------------------------------------------+
//| BÙ VỐN HÀNG TUẦN (Monday phiên mở)                              |
//| Điều kiện: balance < KeepPrincipal, quỹ đủ, key R bật          |
//+------------------------------------------------------------------+
void CheckWeeklyReplenish()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week != 1) return;   // Monday = 1

   int localH = LocalHour(dt);
   int now    = localH * 60 + dt.min;
   int start  = StartHour_GMT7 * 60 + StartMin_GMT7;
   if(now < start || now > start + 5) return;

   if(TimeCurrent() - gLastReplenishWeek < 6 * 86400) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance >= KeepPrincipal) return;

   double needed = KeepPrincipal - balance;
   double replenish = 0.0;

   if(!gFundEverGtPrin)
   {
      // Case A: quỹ chưa bao giờ vượt principal → dùng toàn bộ quỹ
      if(gWithdrawFund <= 0.0) { Print("[NBG] Replenish skip: fund empty"); return; }
      replenish = MathMin(needed, gWithdrawFund);
   }
   else
   {
      // Case B: quỹ đã vượt principal → chỉ dùng phần surplus
      double available = gWithdrawFund - KeepPrincipal;
      if(available <= 0.0) { Print("[NBG] Replenish skip: no surplus in fund"); return; }
      replenish = MathMin(needed, available);
   }

   if(replenish < 0.01) return;

   gWithdrawFund       -= replenish;
   gTotalReplenished   += replenish;
   gMonthlyReplenished += replenish;
   gMthReplenished     += replenish;
   gLastReplenishWeek   = TimeCurrent();

   Print(StringFormat("[NBG] REPLENISH +%.2f | Balance %.2f → %.2f | FundAfter=%.2f",
         replenish, balance, balance + replenish, gWithdrawFund));
   Alert(StringFormat("[NewBotGold] BÙ VỐN $%.2f — Balance %.2f → %.2f",
         replenish, balance, balance + replenish));
   SaveState();
}

//+------------------------------------------------------------------+
//| NEW DAY                                                          |
//+------------------------------------------------------------------+
void CheckNewDay()
{
   static int lastDay = -1;
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day == lastDay) return;
   lastDay = dt.day;

   gTodayStopped  = false;
   gDayHighEquity = AccountInfoDouble(ACCOUNT_EQUITY);

   Print(StringFormat("[NBG] === NEW DAY %04d-%02d-%02d | Balance=%.2f | Fund=%.2f ===",
         dt.year, dt.mon, dt.day, AccountInfoDouble(ACCOUNT_BALANCE), gWithdrawFund));
}

//+------------------------------------------------------------------+
//| NEW MONTH — báo cáo + reset monthly counters                     |
//+------------------------------------------------------------------+
void CheckNewMonth()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.mon == gCurMonth && dt.year == gCurYear) return;

   if(EnableMonthlyRpt) WriteMonthlyReport(gCurYear, gCurMonth);

   gMthCycles      = 0; gMthWin    = 0; gMthLoss   = 0;
   gMthPnl         = 0.0;
   gMthMaxDD       = 0.0;
   gMthHighEq      = AccountInfoDouble(ACCOUNT_EQUITY);
   gMthWithdrawn   = 0.0;
   gMthReplenished = 0.0;
   gMonthlyWithdrawn   = 0.0;
   gMonthlyReplenished = 0.0;

   gWeekNum    = 1;
   gWeekCycles = 0; gWeekWin = 0; gWeekLoss = 0; gWeekPnl = 0.0;

   gCurMonth = dt.mon; gCurYear = dt.year;
}

//+------------------------------------------------------------------+
//| MONTHLY REPORT                                                   |
//+------------------------------------------------------------------+
void WriteMonthlyReport(int year, int mon)
{
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double mthDD_pct = gMthHighEq > 0 ? gMthMaxDD / gMthHighEq * 100.0 : 0.0;
   double mthWR     = gMthCycles > 0 ? (double)gMthWin / gMthCycles * 100.0 : 0.0;
   double allWR     = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   double netW      = gMthWithdrawn - gMthReplenished;

   string D = "==================================================";
   WriteStatsLog("MONTHLY", D);
   WriteStatsLog("MONTHLY", StringFormat("MONTHLY REPORT  %04d-%02d", year, mon));
   WriteStatsLog("MONTHLY", D);
   WriteStatsLog("MONTHLY", StringFormat("Balance         : %10.2f USD", balance));
   WriteStatsLog("MONTHLY", StringFormat("Principal       : %10.2f USD", KeepPrincipal));
   WriteStatsLog("MONTHLY", "--- MONEY MANAGEMENT ---");
   WriteStatsLog("MONTHLY", StringFormat("Rut lai (gross) : %+10.2f USD", gMthWithdrawn));
   WriteStatsLog("MONTHLY", StringFormat("Bu von          : %+10.2f USD", -gMthReplenished));
   WriteStatsLog("MONTHLY", StringFormat(">>> NET RUT     : %+10.2f USD <<<", netW));
   WriteStatsLog("MONTHLY", StringFormat("Quy tich luy    : %10.2f USD", gWithdrawFund));
   WriteStatsLog("MONTHLY", "--- PERFORMANCE ---");
   WriteStatsLog("MONTHLY", StringFormat("PnL thang       : %+10.2f USD", gMthPnl));
   WriteStatsLog("MONTHLY", StringFormat("Cycles thang    : %10d  (W=%d L=%d  WR=%.1f%%)",
            gMthCycles, gMthWin, gMthLoss, mthWR));
   WriteStatsLog("MONTHLY", StringFormat("Max DD thang    : %10.2f USD (%.1f%%)", gMthMaxDD, mthDD_pct));
   WriteStatsLog("MONTHLY", "--- ALL TIME ---");
   WriteStatsLog("MONTHLY", StringFormat("Total PnL       : %+10.2f USD", gTotalPnl));
   WriteStatsLog("MONTHLY", StringFormat("Total Cycles    : %10d  (W=%d L=%d  WR=%.1f%%)",
            gTotalCycles, gWinCycles, gLossCycles, allWR));
   WriteStatsLog("MONTHLY", StringFormat("Net Withdrawn   : %+10.2f USD", gTotalWithdrawn - gTotalReplenished));
   WriteStatsLog("MONTHLY", D);
   Print(StringFormat("[NBG] === MONTHLY REPORT %04d-%02d written to NBG_STATS ===", year, mon));
}

//+------------------------------------------------------------------+
//| LOG CHI TIẾT MỖI CYCLE → NBG_STATS_YYYYMM.log                  |
//+------------------------------------------------------------------+
void LogCycleSummary(double pnl, bool win)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double allWR   = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   string msg = StringFormat(
      "CYCLE #%d | %s | PnL=%+.2f | Balance=%.2f | WinRate=%.1f%% (W=%d L=%d T=%d) | MthPnl=%+.2f",
      gTotalCycles, win?"WIN":"LOSS", pnl, balance, allWR, gWinCycles, gLossCycles, gTotalCycles, gMthPnl);
   Print("[NBG] === " + msg + " ===");
   WriteStatsLog("CYCLE", msg);
}

//+------------------------------------------------------------------+
//| HOURLY PERFORMANCE LOG → NBG_STATS_YYYYMM.log                   |
//+------------------------------------------------------------------+
void LogPerformance()
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   int    cnt = CountBuyPositions();
   double flt = GetTotalFloatingPnl();
   double ddNow = (gDayHighEquity > 0)
                  ? (gDayHighEquity - eq) / gDayHighEquity * 100.0 : 0.0;
   double allWR = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;

   Print(StringFormat("[NBG] HOUR | Bal=%.2f Eq=%.2f DD=%.2f%% | Pos=%d Float=%+.2f | Cyc=%d WR=%.1f%% | Pnl=%+.2f Fund=%.2f",
         bal, eq, ddNow, cnt, flt, gTotalCycles, allWR, gTotalPnl, gWithdrawFund));
}

//+------------------------------------------------------------------+
//| WEEKLY SUMMARY → NBG_STATS_YYYYMM.log                           |
//+------------------------------------------------------------------+
void LogWeeklySummary()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double weekWR  = gWeekCycles > 0 ? (double)gWeekWin / gWeekCycles * 100.0 : 0.0;
   bool   win     = gWeekPnl >= 0.0;
   string msg = StringFormat(
      "WEEK %d | %s | PnL=%+.2f | Balance=%.2f | WinRate=%.1f%% (W=%d L=%d T=%d) | MthPnl=%+.2f",
      gWeekNum, win?"WIN":"LOSS", gWeekPnl, balance, weekWR,
      gWeekWin, gWeekLoss, gWeekCycles, gMthPnl);
   Print("[NBG] === " + msg + " ===");
   WriteStatsLog("WEEK", msg);
   // Reset tuần
   gWeekNum++;
   gWeekCycles = 0; gWeekWin = 0; gWeekLoss = 0; gWeekPnl = 0.0;
}

//+------------------------------------------------------------------+
//| Helper: giờ local GMT+7 từ MqlDateTime server                    |
//+------------------------------------------------------------------+
int LocalHour(const MqlDateTime &dt)
{
   int h = dt.hour + (7 - ServerGMT);
   while(h >= 24) h -= 24;
   while(h <  0)  h += 24;
   return h;
}

//+------------------------------------------------------------------+
//| InTradingWindow (GMT+7, no weekend)                              |
//+------------------------------------------------------------------+
bool InTradingWindow()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 6 || dt.day_of_week == 0) return false;

   int localH = LocalHour(dt);
   int now    = localH * 60 + dt.min;
   int start  = StartHour_GMT7 * 60 + StartMin_GMT7;
   int end    = EndHour_GMT7   * 60 + EndMin_GMT7;

   if(start <= end) return (now >= start && now < end);
   return (now >= start || now < end);
}

//+------------------------------------------------------------------+
//| COUNT BUY POSITIONS                                              |
//+------------------------------------------------------------------+
int CountBuyPositions()
{
   int n = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() == POSITION_TYPE_BUY) n++;
   }
   return n;
}

//+------------------------------------------------------------------+
//| LOWEST OPEN PRICE (grid floor)                                   |
//+------------------------------------------------------------------+
double GetLowestOpenPrice()
{
   double lowest = DBL_MAX;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() != POSITION_TYPE_BUY) continue;
      if(pos.PriceOpen() < lowest) lowest = pos.PriceOpen();
   }
   return (lowest == DBL_MAX) ? 0.0 : lowest;
}

//+------------------------------------------------------------------+
//| TOTAL FLOATING PNL                                               |
//+------------------------------------------------------------------+
double GetTotalFloatingPnl()
{
   double total = 0.0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      total += pos.Profit() + pos.Swap() + pos.Commission();
   }
   return total;
}

//+------------------------------------------------------------------+
//| LOT CỦA LỆNH ĐẦU TIÊN (dùng khi restart giữa chừng)            |
//+------------------------------------------------------------------+
double GetFirstOpenLot()
{
   datetime earliest = D'2100.01.01';
   double   lot      = MinLot;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() != POSITION_TYPE_BUY) continue;
      if(pos.Time() < earliest) { earliest = pos.Time(); lot = pos.Volume(); }
   }
   return lot;
}

//+------------------------------------------------------------------+
//| CALC LOT                                                         |
//+------------------------------------------------------------------+
double CalcLot()
{
   double baseLot = MinLot;
   if(AutoLot)
   {
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      baseLot = MathFloor(balance / BalancePer) * 0.01;
      if(baseLot < MinLot) baseLot = MinLot;
   }
   return NormalizeLot(MathMin(baseLot, MaxLot));
}

//+------------------------------------------------------------------+
//| NORMALIZE LOT                                                    |
//+------------------------------------------------------------------+
double NormalizeLot(double lot)
{
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lot = MathFloor(lot / st) * st;
   return NormalizeDouble(MathMax(mn, MathMin(mx, lot)), 2);
}

//+------------------------------------------------------------------+
//| SAVE STATE (MT5 Global Variables)                                |
//+------------------------------------------------------------------+
void SaveState()
{
   string p = "NBG_";
   GlobalVariableSet(p+"Fund",       gWithdrawFund);
   GlobalVariableSet(p+"TotalW",     gTotalWithdrawn);
   GlobalVariableSet(p+"TotalR",     gTotalReplenished);
   GlobalVariableSet(p+"FundGtP",    gFundEverGtPrin?1.0:0.0);
   GlobalVariableSet(p+"TotalCyc",   gTotalCycles);
   GlobalVariableSet(p+"WinCyc",     gWinCycles);
   GlobalVariableSet(p+"LossCyc",    gLossCycles);
   GlobalVariableSet(p+"TotalPnl",   gTotalPnl);
   GlobalVariableSet(p+"LastDeal",   (double)gLastDealTicket);
   GlobalVariableSet(p+"LastWDay",   (double)gLastWithdrawDay);
   GlobalVariableSet(p+"LastWkC",    (double)gLastWeeklyClose);
   GlobalVariableSet(p+"LastRep",    (double)gLastReplenishWeek);
   GlobalVariableSet(p+"MthCyc",     gMthCycles);
   GlobalVariableSet(p+"MthWin",     gMthWin);
   GlobalVariableSet(p+"MthLoss",    gMthLoss);
   GlobalVariableSet(p+"MthPnl",     gMthPnl);
   GlobalVariableSet(p+"MthMaxDD",   gMthMaxDD);
   GlobalVariableSet(p+"MthHighEq",  gMthHighEq);
   GlobalVariableSet(p+"MthW",       gMthWithdrawn);
   GlobalVariableSet(p+"MthR",       gMthReplenished);
   GlobalVariableSet(p+"MonW",       gMonthlyWithdrawn);
   GlobalVariableSet(p+"MonR",       gMonthlyReplenished);
   GlobalVariableSet(p+"CurMon",     gCurMonth);
   GlobalVariableSet(p+"CurYear",    gCurYear);
}

//+------------------------------------------------------------------+
//| LOAD STATE                                                       |
//+------------------------------------------------------------------+
void LoadState()
{
   string p = "NBG_";
   if(GlobalVariableCheck(p+"Fund"))      gWithdrawFund       = GlobalVariableGet(p+"Fund");
   if(GlobalVariableCheck(p+"TotalW"))    gTotalWithdrawn     = GlobalVariableGet(p+"TotalW");
   if(GlobalVariableCheck(p+"TotalR"))    gTotalReplenished   = GlobalVariableGet(p+"TotalR");
   if(GlobalVariableCheck(p+"FundGtP"))   gFundEverGtPrin     = GlobalVariableGet(p+"FundGtP") > 0.5;
   if(GlobalVariableCheck(p+"TotalCyc"))  gTotalCycles        = (int)GlobalVariableGet(p+"TotalCyc");
   if(GlobalVariableCheck(p+"WinCyc"))    gWinCycles          = (int)GlobalVariableGet(p+"WinCyc");
   if(GlobalVariableCheck(p+"LossCyc"))   gLossCycles         = (int)GlobalVariableGet(p+"LossCyc");
   if(GlobalVariableCheck(p+"TotalPnl"))  gTotalPnl           = GlobalVariableGet(p+"TotalPnl");
   if(GlobalVariableCheck(p+"LastDeal"))  gLastDealTicket     = (ulong)GlobalVariableGet(p+"LastDeal");
   if(GlobalVariableCheck(p+"LastWDay"))  gLastWithdrawDay    = (datetime)GlobalVariableGet(p+"LastWDay");
   if(GlobalVariableCheck(p+"LastWkC"))   gLastWeeklyClose    = (datetime)GlobalVariableGet(p+"LastWkC");
   if(GlobalVariableCheck(p+"LastRep"))   gLastReplenishWeek  = (datetime)GlobalVariableGet(p+"LastRep");
   if(GlobalVariableCheck(p+"MthCyc"))    gMthCycles          = (int)GlobalVariableGet(p+"MthCyc");
   if(GlobalVariableCheck(p+"MthWin"))    gMthWin             = (int)GlobalVariableGet(p+"MthWin");
   if(GlobalVariableCheck(p+"MthLoss"))   gMthLoss            = (int)GlobalVariableGet(p+"MthLoss");
   if(GlobalVariableCheck(p+"MthPnl"))    gMthPnl             = GlobalVariableGet(p+"MthPnl");
   if(GlobalVariableCheck(p+"MthMaxDD"))  gMthMaxDD           = GlobalVariableGet(p+"MthMaxDD");
   if(GlobalVariableCheck(p+"MthHighEq")) gMthHighEq          = GlobalVariableGet(p+"MthHighEq");
   if(GlobalVariableCheck(p+"MthW"))      gMthWithdrawn       = GlobalVariableGet(p+"MthW");
   if(GlobalVariableCheck(p+"MthR"))      gMthReplenished     = GlobalVariableGet(p+"MthR");
   if(GlobalVariableCheck(p+"MonW"))      gMonthlyWithdrawn   = GlobalVariableGet(p+"MonW");
   if(GlobalVariableCheck(p+"MonR"))      gMonthlyReplenished = GlobalVariableGet(p+"MonR");
   if(GlobalVariableCheck(p+"CurMon"))    gCurMonth           = (int)GlobalVariableGet(p+"CurMon");
   if(GlobalVariableCheck(p+"CurYear"))   gCurYear            = (int)GlobalVariableGet(p+"CurYear");
}

//+------------------------------------------------------------------+
//| FINAL STATS REPORT — ghi khi kết thúc (Deinit / OnTester)      |
//+------------------------------------------------------------------+
void WriteFinalStatsReport()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double allWR   = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   double mthWR   = gMthCycles   > 0 ? (double)gMthWin    / gMthCycles   * 100.0 : 0.0;
   double netW    = gTotalWithdrawn - gTotalReplenished;
   double mthDD_p = gMthHighEq > 0 ? gMthMaxDD / gMthHighEq * 100.0 : 0.0;

   Print(StringFormat("[NBG] === FINAL | Bal=%.2f Eq=%.2f | Cyc=%d WR=%.1f%% | Pnl=%+.2f | NetW=%+.2f ===",
         balance, equity, gTotalCycles, allWR, gTotalPnl, netW));
}

//+------------------------------------------------------------------+
//| WRITE STATS LOG → NBG_STATS_YYYYMM.log (riêng phần thống kê)   |
//| Tester: ghi vào Tester\Files\                                    |
//| Live  : ghi vào Common\Files\NBG\                               |
//+------------------------------------------------------------------+
void WriteStatsLog(string tag, string msg)
{
   MqlDateTime _dt; TimeToStruct(TimeCurrent(), _dt);
   int yr  = gCurYear  > 0 ? gCurYear  : _dt.year;
   int mon = gCurMonth > 0 ? gCurMonth : _dt.mon;

   int    flags;
   string fname;
   if(gIsTester)
   {
      fname = StringFormat("NBG_STATS_%04d%02d.log", yr, mon);
      flags = FILE_WRITE|FILE_READ|FILE_TXT|FILE_ANSI;
   }
   else
   {
      fname = StringFormat("NBG\\NBG_STATS_%04d%02d.log", yr, mon);
      flags = FILE_WRITE|FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI;
   }

   int handle = FileOpen(fname, flags, '\n');
   if(handle == INVALID_HANDLE) return;
   FileSeek(handle, 0, SEEK_END);
   FileWriteString(handle, StringFormat("[%s] [%s] %s\n", TimeToString(TimeCurrent()), tag, msg));
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| WRITE LOG → NBG_YYYYMM.log (trade log chính)                    |
//| Tester: Tester\Files\ | Live: Common\Files\NBG\                 |
//+------------------------------------------------------------------+
void WriteLog(string tag, string msg)
{
   MqlDateTime _dt; TimeToStruct(TimeCurrent(), _dt);
   int yr  = gCurYear  > 0 ? gCurYear  : _dt.year;
   int mon = gCurMonth > 0 ? gCurMonth : _dt.mon;

   int    flags;
   string fname;
   if(gIsTester)
   {
      fname = StringFormat("NBG_%04d%02d.log", yr, mon);
      flags = FILE_WRITE|FILE_READ|FILE_TXT|FILE_ANSI;
   }
   else
   {
      fname = StringFormat("NBG\\NBG_%04d%02d.log", yr, mon);
      flags = FILE_WRITE|FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI;
   }

   int handle = FileOpen(fname, flags, '\n');
   if(handle == INVALID_HANDLE) return;
   FileSeek(handle, 0, SEEK_END);
   FileWriteString(handle, StringFormat("[%s] [%s] %s\n", TimeToString(TimeCurrent()), tag, msg));
   FileClose(handle);
   if(tag == "INFO" && VerboseLog) Print("[NBG] " + msg);
}
//+------------------------------------------------------------------+
