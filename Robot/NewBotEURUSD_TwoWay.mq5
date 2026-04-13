//+------------------------------------------------------------------+
//|                          NewBotEURUSD_TwoWay.mq5                  |
//|         BUY + SELL Martingale Grid — EURUSD — DAY TRADING         |
//|         AutoProject V2 | v1.00                                    |
//|                                                                   |
//|  Adapted từ NewBotGold_V2_DayTrading v2.10 cho EURUSD:            |
//|  • GridStep tính bằng PIP (1 pip = 10 pts = 0.0001)              |
//|  • Phiên London + NY: 15:00–23:00 GMT+7                          |
//|  • MaxSpread_Pts = 20 pts (2 pip Standard Exness)                |
//|  • BUY+SELL hai chiều độc lập song song                          |
//|  • TP từng side: float >= X% balance → đóng side đó             |
//|  • EOD force-close, NoNewOrder, DD guard, StopBot halt            |
//+------------------------------------------------------------------+
#property copyright "AutoProject V2"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//=== LOT ===
input group "=== LOT ==="
input bool   AutoLot         = true;     // Scale lot với balance (compounding)
input double BalancePer      = 200.0;    // Mỗi $X → 0.01 lot (EURUSD: $200→0.01)
input double MinLot          = 0.01;     // Lot tối thiểu
input double MaxLot          = 2.00;     // Lot tối đa (safety cap)

//=== GRID STRATEGY ===
input group "=== GRID STRATEGY ==="
input double GridStep_Pips   = 10.0;    // Grid step (pip) — giá thay đổi X pip → nhồi thêm
input int    MaxPositions    = 10;      // Tối đa lệnh / cycle / side
input double TakeProfit_Pct  = 20.0;   // Đóng side khi floating >= X% balance
input bool   EnableBuy       = true;    // Bật BUY grid (toggle key B)
input bool   EnableSell      = true;    // Bật SELL grid (toggle key S)

//=== SPREAD ===
input group "=== SPREAD ==="
input int    MaxSpread_Pts   = 20;      // Max spread (points) — 2 pip cho Standard Exness

//=== TIME WINDOW (GMT+7) ===
input group "=== TIME WINDOW (GMT+7) ==="
// London: 08:00 UTC = 15:00 GMT+7 | NY close: 17:00 UTC = 00:00 GMT+7
input int    StartHour_GMT7  = 15;     // 15:00 GMT+7 = London open (08:00 UTC)
input int    StartMin_GMT7   = 0;
input int    EndHour_GMT7    = 23;     // 23:00 GMT+7 = trước NY close
input int    EndMin_GMT7     = 0;
input int    ServerGMT       = 3;      // Exness server GMT+3

//=== DAY TRADING ===
input group "=== DAY TRADING ==="
input int    ForceCloseMin   = 5;      // Đóng tất cả lệnh trước X phút khi hết phiên
input int    NoNewOrderMin   = 10;     // Ngừng mở lệnh mới trước X phút khi hết phiên

//=== RISK MANAGEMENT ===
input group "=== RISK MANAGEMENT ==="
input double MaxDDPct        = 20.0;   // Max daily drawdown % → dừng hôm nay
input double StopBotPct      = 50.0;   // Dừng vĩnh viễn nếu balance < X% vốn ban đầu

//=== MONEY MANAGEMENT ===
input group "=== MONEY MANAGEMENT ==="
input double KeepPrincipal   = 100.0;  // Vốn gốc giữ lại (USD)
input double WithdrawRatio   = 0.50;   // Rút X% lãi hàng ngày (Mon–Thu)
input bool   EnableWithdraw  = true;   // Bật rút lãi hàng ngày (toggle key W)
input bool   EnableReplenish = true;   // Bật bù vốn thứ Hai (toggle key R)
input bool   EnableMonthlyRpt= true;   // Báo cáo tháng tự động

//=== LOGGING ===
input group "=== LOGGING ==="
input bool   VerboseLog      = true;

//------------------------------------------------------------------
// Globals — Trading
CTrade        trade;
CPositionInfo pos;

int      gMagic         = 20240204;    // EURUSD Two-Way magic
string   gComment       = "NBE_TW";   // EUR TwoWay tag

double   gStartBalance  = 0.0;
double   gDayHighEquity = 0.0;
bool     gTodayStopped  = false;
bool     gBotHalted     = false;
datetime gLastCloseBuy  = 0;
datetime gLastCloseSell = 0;
double   gCycleLotBuy   = 0.0;
double   gCycleLotSell  = 0.0;
double   gGridStepPrice = 0.0;         // GridStep_Pips → price (computed in OnInit)

bool     gEodFiredToday = false;

//------------------------------------------------------------------
// Globals — Money Management
double   gWithdrawFund      = 0.0;
double   gTotalWithdrawn    = 0.0;
double   gTotalReplenished  = 0.0;
double   gMonthlyWithdrawn  = 0.0;
double   gMonthlyReplenished= 0.0;
bool     gFundEverGtPrin    = false;
datetime gLastWithdrawDay   = 0;
datetime gLastWeeklyClose   = 0;
datetime gLastReplenishWeek = 0;

// Runtime toggles (key W / R / B / S)
bool     gWithdrawOn  = true;
bool     gReplenishOn = true;
bool     gBuyOn       = true;
bool     gSellOn      = true;

//------------------------------------------------------------------
// Globals — Stats (All-time)
int      gTotalCycles   = 0;
int      gWinCycles     = 0;
int      gLossCycles    = 0;
double   gTotalPnl      = 0.0;
ulong    gLastDealTicket= 0;

// Stats — Monthly
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

// Stats — Weekly
int      gWeekNum       = 1;
int      gWeekCycles    = 0;
int      gWeekWin       = 0;
int      gWeekLoss      = 0;
double   gWeekPnl       = 0.0;

bool     gIsTester      = false;

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   gIsTester = MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION);

   // Validate symbol — warn if không phải EURUSD
   if(StringFind(_Symbol, "EURUSD") < 0 && StringFind(_Symbol, "EURUSDm") < 0)
      Print(StringFormat("[NBE_TW] WARNING: Symbol=%s — bot thiết kế cho EURUSD!", _Symbol));

   // GridStep: pips → price. EURUSD 5-digit: 1 pip = 10 points = 0.0001
   gGridStepPrice = GridStep_Pips * 10.0 * _Point;

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
   gBuyOn       = EnableBuy;
   gSellOn      = EnableSell;

   // Resume mid-cycle
   int cntB = CountBuyPositions();
   int cntS = CountSellPositions();
   if(cntB > 0)
   {
      gCycleLotBuy = GetFirstLot(POSITION_TYPE_BUY);
      Print(StringFormat("[NBE_TW] Resumed BUY mid-cycle: %d pos | lot=%.2f", cntB, gCycleLotBuy));
   }
   if(cntS > 0)
   {
      gCycleLotSell = GetFirstLot(POSITION_TYPE_SELL);
      Print(StringFormat("[NBE_TW] Resumed SELL mid-cycle: %d pos | lot=%.2f", cntS, gCycleLotSell));
   }

   FolderCreate("NBG", FILE_COMMON);
   EventSetTimer(60);

   Print(StringFormat("[NBE_TW] Init OK | EURUSD TWO-WAY | Balance=%.2f | GridStep=%.1f pip (%.5f) | TP=%.0f%% | ForceClose=%dmin | NoNew=%dmin",
         gStartBalance, GridStep_Pips, gGridStepPrice, TakeProfit_Pct, ForceCloseMin, NoNewOrderMin));
   Print(StringFormat("[NBE_TW] Session GMT+7: %02d:00–%02d:00 | MaxSpread=%d pts | BUY=%s SELL=%s",
         StartHour_GMT7, EndHour_GMT7, MaxSpread_Pts,
         gBuyOn?"ON":"OFF", gSellOn?"ON":"OFF"));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   WriteFinalStatsReport();
   if(!gIsTester) SaveState();
   Print(StringFormat("[NBE_TW] Deinit reason=%d | Cycles=%d | TotalPnl=%+.2f | Fund=%.2f",
         reason, gTotalCycles, gTotalPnl, gWithdrawFund));
}

//+------------------------------------------------------------------+
//| OnTester                                                         |
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

   double mthDD = gMthHighEq - equity;
   if(mthDD > gMthMaxDD) gMthMaxDD = mthDD;

   CheckNewDay();
   CheckNewMonth();

   // Permanent halt
   if(balance < gStartBalance * StopBotPct / 100.0)
   {
      CloseAll("HALT: balance < " + DoubleToString(StopBotPct, 0) + "% of start");
      gBotHalted = true;
      Alert(StringFormat("[NBE_TW] HALTED — balance %.2f below %.0f%% of start %.2f",
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

   // EOD force-close
   CheckEndOfDay();

   // TP check — mỗi side độc lập
   CheckTakeProfit();

   // Trading window + spread filter
   if(!InTradingWindow()) return;
   if(IsNearSessionEnd(NoNewOrderMin)) return;

   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > MaxSpread_Pts)
   {
      if(VerboseLog)
         Print(StringFormat("[NBE_TW] Spread=%d pts > max=%d — skip", spread, MaxSpread_Pts));
      return;
   }

   RunGrid();
}

//+------------------------------------------------------------------+
//| Timer — money mgmt + hourly stats                                |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(gBotHalted) return;

   if(gWithdrawOn)   CheckDailyWithdraw();
   if(gWithdrawOn)   CheckWeeklyWithdraw();
   if(gReplenishOn)  CheckWeeklyReplenish();

   static datetime lastStatTime = 0;
   if(TimeCurrent() - lastStatTime >= 3600)
   {
      LogPerformance();
      lastStatTime = TimeCurrent();
   }
   SaveState();
}

//+------------------------------------------------------------------+
//| OnTrade — capture realized PnL                                   |
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
//| Key W / R / B / S                                                |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_KEYDOWN) return;
   if(lparam == 87)        // W
   {
      gWithdrawOn = !gWithdrawOn;
      Print(StringFormat("[NBE_TW] Withdraw: %s", gWithdrawOn?"ON":"OFF"));
      Alert(StringFormat("[NBE_TW] Withdraw: %s", gWithdrawOn?"ON":"OFF"));
   }
   else if(lparam == 82)   // R
   {
      gReplenishOn = !gReplenishOn;
      Print(StringFormat("[NBE_TW] Replenish: %s", gReplenishOn?"ON":"OFF"));
      Alert(StringFormat("[NBE_TW] Replenish: %s", gReplenishOn?"ON":"OFF"));
   }
   else if(lparam == 66)   // B
   {
      gBuyOn = !gBuyOn;
      Print(StringFormat("[NBE_TW] BUY grid: %s", gBuyOn?"ON":"OFF"));
      Alert(StringFormat("[NBE_TW] BUY grid: %s", gBuyOn?"ON":"OFF"));
   }
   else if(lparam == 83)   // S
   {
      gSellOn = !gSellOn;
      Print(StringFormat("[NBE_TW] SELL grid: %s", gSellOn?"ON":"OFF"));
      Alert(StringFormat("[NBE_TW] SELL grid: %s", gSellOn?"ON":"OFF"));
   }
}

//+------------------------------------------------------------------+
//| EOD — đóng CẢ HAI side trước khi hết phiên (1 lần/ngày)        |
//+------------------------------------------------------------------+
void CheckEndOfDay()
{
   if(gEodFiredToday) return;
   if(CountBuyPositions() == 0 && CountSellPositions() == 0) return;
   if(!IsNearSessionEnd(ForceCloseMin)) return;

   double floatBuy  = GetBuyFloatingPnl();
   double floatSell = GetSellFloatingPnl();
   double totalFloat= floatBuy + floatSell;
   bool   win       = (totalFloat >= 0.0);

   Print(StringFormat("[NBE_TW] EOD FORCE CLOSE | FloatBUY=%+.2f FloatSELL=%+.2f Total=%+.2f | BuyPos=%d SellPos=%d | %s",
         floatBuy, floatSell, totalFloat,
         CountBuyPositions(), CountSellPositions(),
         win ? "WIN" : "LOSS"));

   CloseAll("EOD FORCE CLOSE");

   gTotalCycles++; gMthCycles++; gWeekCycles++;
   if(win) { gWinCycles++;  gMthWin++;  gWeekWin++;  }
   else    { gLossCycles++; gMthLoss++; gWeekLoss++; }
   gWeekPnl += totalFloat;
   LogCycleSummary(totalFloat, win, "EOD");

   gEodFiredToday = true;
   gTodayStopped  = true;
}

//+------------------------------------------------------------------+
//| TAKE PROFIT — từng side độc lập                                  |
//+------------------------------------------------------------------+
void CheckTakeProfit()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double target  = balance * TakeProfit_Pct / 100.0;

   // ── BUY side ──────────────────────────────────────────────────
   int cntBuy = CountBuyPositions();
   if(cntBuy > 0)
   {
      double floatBuy = GetBuyFloatingPnl();
      if(floatBuy >= target)
      {
         Print(StringFormat("[NBE_TW] TP BUY | Float=%+.2f >= Target=%.2f (%.0f%% × %.2f) | Pos=%d",
               floatBuy, target, TakeProfit_Pct, balance, cntBuy));
         CloseSide(POSITION_TYPE_BUY, "TP BUY");
         gTotalCycles++; gMthCycles++; gWeekCycles++;
         gWinCycles++;   gMthWin++;    gWeekWin++;
         gWeekPnl += floatBuy;
         LogCycleSummary(floatBuy, true, "BUY");
      }
   }

   // ── SELL side ─────────────────────────────────────────────────
   int cntSell = CountSellPositions();
   if(cntSell > 0)
   {
      double floatSell = GetSellFloatingPnl();
      if(floatSell >= target)
      {
         Print(StringFormat("[NBE_TW] TP SELL | Float=%+.2f >= Target=%.2f (%.0f%% × %.2f) | Pos=%d",
               floatSell, target, TakeProfit_Pct, balance, cntSell));
         CloseSide(POSITION_TYPE_SELL, "TP SELL");
         gTotalCycles++; gMthCycles++; gWeekCycles++;
         gWinCycles++;   gMthWin++;    gWeekWin++;
         gWeekPnl += floatSell;
         LogCycleSummary(floatSell, true, "SELL");
      }
   }
}

//+------------------------------------------------------------------+
//| GRID — BUY và SELL song song độc lập                            |
//| EURUSD: grid trigger theo pips (gGridStepPrice = pip*10*_Point) |
//+------------------------------------------------------------------+
void RunGrid()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // ── BUY side: nhồi thêm BUY khi giá giảm >= GridStep pips ────
   int  cntBuy  = CountBuyPositions();
   bool buySkip = !gBuyOn || (cntBuy == 0 && gLastCloseBuy > 0 && TimeCurrent() - gLastCloseBuy < 10);
   if(!buySkip)
   {
      if(cntBuy == 0)
      {
         gCycleLotBuy = CalcLot();
         if(VerboseLog)
            Print(StringFormat("[NBE_TW] NEW BUY CYCLE | Lot=%.2f | Bid=%.5f", gCycleLotBuy, bid));
         OpenBuy(1);
      }
      else if(cntBuy < MaxPositions)
      {
         double lowestBuy = GetLowestOpenPrice();
         if(lowestBuy > 0 && bid <= lowestBuy - gGridStepPrice)
         {
            if(VerboseLog)
               Print(StringFormat("[NBE_TW] GRID ADD BUY #%d | bid=%.5f | lowest=%.5f | drop=%.1f pip",
                     cntBuy+1, bid, lowestBuy, (lowestBuy-bid)/gGridStepPrice*GridStep_Pips));
            OpenBuy(cntBuy + 1);
         }
      }
   }

   // ── SELL side: nhồi thêm SELL khi giá tăng >= GridStep pips ──
   int  cntSell  = CountSellPositions();
   bool sellSkip = !gSellOn || (cntSell == 0 && gLastCloseSell > 0 && TimeCurrent() - gLastCloseSell < 10);
   if(!sellSkip)
   {
      if(cntSell == 0)
      {
         gCycleLotSell = CalcLot();
         if(VerboseLog)
            Print(StringFormat("[NBE_TW] NEW SELL CYCLE | Lot=%.2f | Ask=%.5f", gCycleLotSell, ask));
         OpenSell(1);
      }
      else if(cntSell < MaxPositions)
      {
         double highestSell = GetHighestOpenPrice();
         if(highestSell > 0 && ask >= highestSell + gGridStepPrice)
         {
            if(VerboseLog)
               Print(StringFormat("[NBE_TW] GRID ADD SELL #%d | ask=%.5f | highest=%.5f | rise=%.1f pip",
                     cntSell+1, ask, highestSell, (ask-highestSell)/gGridStepPrice*GridStep_Pips));
            OpenSell(cntSell + 1);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Open BUY                                                         |
//+------------------------------------------------------------------+
void OpenBuy(int posNum)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   string cmt = StringFormat("%s_B%d", gComment, posNum);
   bool ok = trade.Buy(gCycleLotBuy, _Symbol, ask, 0, 0, cmt);
   if(ok)
      Print(StringFormat("[NBE_TW] BUY #%d | Lot=%.2f | Ask=%.5f | Spread=%d pts",
            posNum, gCycleLotBuy, ask, (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   else
   {
      Print(StringFormat("[NBE_TW] BUY FAILED #%d | err=%d | %s",
            posNum, GetLastError(), trade.ResultComment()));
      gLastCloseBuy = TimeCurrent();
   }
}

//+------------------------------------------------------------------+
//| Open SELL                                                        |
//+------------------------------------------------------------------+
void OpenSell(int posNum)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   string cmt = StringFormat("%s_S%d", gComment, posNum);
   bool ok = trade.Sell(gCycleLotSell, _Symbol, bid, 0, 0, cmt);
   if(ok)
      Print(StringFormat("[NBE_TW] SELL #%d | Lot=%.2f | Bid=%.5f | Spread=%d pts",
            posNum, gCycleLotSell, bid, (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   else
   {
      Print(StringFormat("[NBE_TW] SELL FAILED #%d | err=%d | %s",
            posNum, GetLastError(), trade.ResultComment()));
      gLastCloseSell = TimeCurrent();
   }
}

//+------------------------------------------------------------------+
//| Đóng một side (BUY hoặc SELL)                                   |
//+------------------------------------------------------------------+
void CloseSide(ENUM_POSITION_TYPE side, string reason)
{
   Print(StringFormat("[NBE_TW] CloseSide(%s): %s",
         side == POSITION_TYPE_BUY ? "BUY" : "SELL", reason));
   for(int attempt = 1; attempt <= 5; attempt++)
   {
      ulong tickets[]; int n = 0;
      for(int i = PositionsTotal()-1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i)) continue;
         if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
         if(pos.PositionType() != side) continue;
         ArrayResize(tickets, n+1); tickets[n++] = pos.Ticket();
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
   if(side == POSITION_TYPE_BUY)  { gCycleLotBuy  = 0.0; gLastCloseBuy  = TimeCurrent(); }
   else                            { gCycleLotSell = 0.0; gLastCloseSell = TimeCurrent(); }
   Print(StringFormat("[NBE_TW] CloseSide(%s) OK",
         side == POSITION_TYPE_BUY ? "BUY" : "SELL"));
}

//+------------------------------------------------------------------+
//| Close All — retry 5 lần                                         |
//+------------------------------------------------------------------+
void CloseAll(string reason)
{
   Print("[NBE_TW] CloseAll: " + reason);
   for(int attempt = 1; attempt <= 5; attempt++)
   {
      ulong tickets[]; int n = 0;
      for(int i = PositionsTotal()-1; i >= 0; i--)
      {
         if(!pos.SelectByIndex(i)) continue;
         if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
         ArrayResize(tickets, n+1); tickets[n++] = pos.Ticket();
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
   gCycleLotBuy   = 0.0; gCycleLotSell  = 0.0;
   gLastCloseBuy  = TimeCurrent(); gLastCloseSell = TimeCurrent();

   int rem = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() == gMagic && pos.Symbol() == _Symbol) rem++;
   }
   if(rem > 0) Print(StringFormat("[NBE_TW] CloseAll WARN: %d still open", rem));
   else        Print("[NBE_TW] CloseAll OK");
}

//+------------------------------------------------------------------+
//| COUNT BUY / SELL POSITIONS                                       |
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

int CountSellPositions()
{
   int n = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() == POSITION_TYPE_SELL) n++;
   }
   return n;
}

//+------------------------------------------------------------------+
//| LOWEST OPEN PRICE (BUY) / HIGHEST OPEN PRICE (SELL)             |
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

double GetHighestOpenPrice()
{
   double highest = -DBL_MAX;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() != POSITION_TYPE_SELL) continue;
      if(pos.PriceOpen() > highest) highest = pos.PriceOpen();
   }
   return (highest == -DBL_MAX) ? 0.0 : highest;
}

//+------------------------------------------------------------------+
//| FLOATING PNL                                                     |
//+------------------------------------------------------------------+
double GetBuyFloatingPnl()
{
   double total = 0.0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() != POSITION_TYPE_BUY) continue;
      total += pos.Profit() + pos.Swap() + pos.Commission();
   }
   return total;
}

double GetSellFloatingPnl()
{
   double total = 0.0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() != POSITION_TYPE_SELL) continue;
      total += pos.Profit() + pos.Swap() + pos.Commission();
   }
   return total;
}

//+------------------------------------------------------------------+
//| LOT ĐẦU TIÊN (resume mid-cycle)                                 |
//+------------------------------------------------------------------+
double GetFirstLot(ENUM_POSITION_TYPE side)
{
   datetime earliest = D'2100.01.01';
   double   lot      = MinLot;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Magic() != gMagic || pos.Symbol() != _Symbol) continue;
      if(pos.PositionType() != side) continue;
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

double NormalizeLot(double lot)
{
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lot = MathFloor(lot / st) * st;
   return NormalizeDouble(MathMax(mn, MathMin(mx, lot)), 2);
}

//+------------------------------------------------------------------+
//| TIMING HELPERS                                                   |
//+------------------------------------------------------------------+
int LocalHour(const MqlDateTime &dt)
{
   int h = dt.hour + (7 - ServerGMT);
   while(h >= 24) h -= 24;
   while(h <  0)  h += 24;
   return h;
}

bool InTradingWindow()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 6 || dt.day_of_week == 0) return false;
   int localH = LocalHour(dt);
   int now    = localH * 60 + dt.min;
   int start  = StartHour_GMT7 * 60 + StartMin_GMT7;
   int end    = EndHour_GMT7   * 60 + EndMin_GMT7;
   if(start <= end) return (now >= start && now < end);
   return (now >= start || now < end);
}

bool IsNearSessionEnd(int minutesBefore)
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 6 || dt.day_of_week == 0) return false;
   int localH = LocalHour(dt);
   int now    = localH * 60 + dt.min;
   int end    = EndHour_GMT7 * 60 + EndMin_GMT7;
   return (now >= end - minutesBefore && now <= end + 2);
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
   gEodFiredToday = false;
   gDayHighEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   Print(StringFormat("[NBE_TW] === NEW DAY %04d-%02d-%02d | Balance=%.2f | Fund=%.2f ===",
         dt.year, dt.mon, dt.day, AccountInfoDouble(ACCOUNT_BALANCE), gWithdrawFund));
}

//+------------------------------------------------------------------+
//| NEW MONTH                                                        |
//+------------------------------------------------------------------+
void CheckNewMonth()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.mon == gCurMonth && dt.year == gCurYear) return;
   if(EnableMonthlyRpt) WriteMonthlyReport(gCurYear, gCurMonth);
   gMthCycles = 0; gMthWin = 0; gMthLoss = 0;
   gMthPnl = 0.0; gMthMaxDD = 0.0;
   gMthHighEq = AccountInfoDouble(ACCOUNT_EQUITY);
   gMthWithdrawn = 0.0; gMthReplenished = 0.0;
   gMonthlyWithdrawn = 0.0; gMonthlyReplenished = 0.0;
   gWeekNum = 1; gWeekCycles = 0; gWeekWin = 0; gWeekLoss = 0; gWeekPnl = 0.0;
   gCurMonth = dt.mon; gCurYear = dt.year;
}

//+------------------------------------------------------------------+
//| MONEY MANAGEMENT                                                 |
//+------------------------------------------------------------------+
void CheckDailyWithdraw()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week < 1 || dt.day_of_week > 4) return;
   int localH = LocalHour(dt);
   int now = localH * 60 + dt.min;
   int end = EndHour_GMT7 * 60 + EndMin_GMT7;
   if(now < end || now > end + 5) return;
   MqlDateTime lw; TimeToStruct(gLastWithdrawDay, lw);
   if(lw.day == dt.day && lw.mon == dt.mon && lw.year == dt.year) return;
   if(CountBuyPositions() + CountSellPositions() > 0) return;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= KeepPrincipal) return;
   double amount = (balance - KeepPrincipal) * WithdrawRatio;
   if(amount < 0.01) return;
   gWithdrawFund += amount; gTotalWithdrawn += amount;
   gMonthlyWithdrawn += amount; gMthWithdrawn += amount;
   gLastWithdrawDay = TimeCurrent();
   if(gWithdrawFund > KeepPrincipal) gFundEverGtPrin = true;
   Print(StringFormat("[NBE_TW] DAILY WITHDRAW +%.2f | Balance=%.2f | Fund=%.2f", amount, balance, gWithdrawFund));
   Alert(StringFormat("[NBE_TW] RUT LAI NGAY $%.2f", amount));
   SaveState();
}

void CheckWeeklyWithdraw()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week != 5) return;
   int localH = LocalHour(dt);
   int now = localH * 60 + dt.min;
   int end = EndHour_GMT7 * 60 + EndMin_GMT7;
   if(now < end || now > end + 5) return;
   if(TimeCurrent() - gLastWeeklyClose < 6 * 86400) return;
   if(CountBuyPositions() + CountSellPositions() > 0) return;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= KeepPrincipal) return;
   double amount = balance - KeepPrincipal;
   if(amount < 0.01) return;
   gWithdrawFund += amount; gTotalWithdrawn += amount;
   gMonthlyWithdrawn += amount; gMthWithdrawn += amount;
   gLastWeeklyClose = TimeCurrent();
   if(gWithdrawFund > KeepPrincipal) gFundEverGtPrin = true;
   Print(StringFormat("[NBE_TW] WEEKLY WITHDRAW +%.2f | Balance %.2f → %.2f | Fund=%.2f", amount, balance, KeepPrincipal, gWithdrawFund));
   Alert(StringFormat("[NBE_TW] RUT CUOI TUAN $%.2f", amount));
   LogWeeklySummary();
   SaveState();
}

void CheckWeeklyReplenish()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week != 1) return;
   int localH = LocalHour(dt);
   int now = localH * 60 + dt.min;
   int start = StartHour_GMT7 * 60 + StartMin_GMT7;
   if(now < start || now > start + 5) return;
   if(TimeCurrent() - gLastReplenishWeek < 6 * 86400) return;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance >= KeepPrincipal) return;
   double needed = KeepPrincipal - balance;
   double replenish = 0.0;
   if(!gFundEverGtPrin)
   {
      if(gWithdrawFund <= 0.0) { Print("[NBE_TW] Replenish skip: fund empty"); return; }
      replenish = MathMin(needed, gWithdrawFund);
   }
   else
   {
      double available = gWithdrawFund - KeepPrincipal;
      if(available <= 0.0) { Print("[NBE_TW] Replenish skip: no surplus"); return; }
      replenish = MathMin(needed, available);
   }
   if(replenish < 0.01) return;
   gWithdrawFund -= replenish; gTotalReplenished += replenish;
   gMonthlyReplenished += replenish; gMthReplenished += replenish;
   gLastReplenishWeek = TimeCurrent();
   Print(StringFormat("[NBE_TW] REPLENISH +%.2f | FundAfter=%.2f", replenish, gWithdrawFund));
   Alert(StringFormat("[NBE_TW] BU VON $%.2f", replenish));
   SaveState();
}

//+------------------------------------------------------------------+
//| LOGGING                                                          |
//+------------------------------------------------------------------+
void LogCycleSummary(double pnl, bool win, string side)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double allWR   = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   string msg = StringFormat(
      "CYCLE #%d [%s] | %s | PnL=%+.2f | Balance=%.2f | WR=%.1f%% (W=%d L=%d T=%d) | MthPnl=%+.2f",
      gTotalCycles, side, win?"WIN":"LOSS", pnl, balance, allWR, gWinCycles, gLossCycles, gTotalCycles, gMthPnl);
   Print("[NBE_TW] === " + msg + " ===");
   WriteStatsLog("CYCLE", msg);
}

void LogPerformance()
{
   double bal   = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq    = AccountInfoDouble(ACCOUNT_EQUITY);
   int    cntB  = CountBuyPositions();
   int    cntS  = CountSellPositions();
   double fltB  = GetBuyFloatingPnl();
   double fltS  = GetSellFloatingPnl();
   double ddNow = (gDayHighEquity > 0) ? (gDayHighEquity - eq) / gDayHighEquity * 100.0 : 0.0;
   double allWR = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   Print(StringFormat("[NBE_TW] HOUR | Bal=%.2f Eq=%.2f DD=%.2f%% | B=%d(%.2f) S=%d(%.2f) | Cyc=%d WR=%.1f%% | Pnl=%+.2f Fund=%.2f",
         bal, eq, ddNow, cntB, fltB, cntS, fltS, gTotalCycles, allWR, gTotalPnl, gWithdrawFund));
}

void LogWeeklySummary()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double weekWR  = gWeekCycles > 0 ? (double)gWeekWin / gWeekCycles * 100.0 : 0.0;
   bool   win     = gWeekPnl >= 0.0;
   string msg = StringFormat("WEEK %d | %s | PnL=%+.2f | Balance=%.2f | WR=%.1f%% (W=%d L=%d T=%d)",
      gWeekNum, win?"WIN":"LOSS", gWeekPnl, balance, weekWR, gWeekWin, gWeekLoss, gWeekCycles);
   Print("[NBE_TW] === " + msg + " ===");
   WriteStatsLog("WEEK", msg);
   gWeekNum++;
   gWeekCycles = 0; gWeekWin = 0; gWeekLoss = 0; gWeekPnl = 0.0;
}

void WriteMonthlyReport(int year, int mon)
{
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double mthDD_pct = gMthHighEq > 0 ? gMthMaxDD / gMthHighEq * 100.0 : 0.0;
   double mthWR     = gMthCycles > 0 ? (double)gMthWin / gMthCycles * 100.0 : 0.0;
   double allWR     = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   string D = "==================================================";
   WriteStatsLog("MONTHLY", D);
   WriteStatsLog("MONTHLY", StringFormat("MONTHLY %04d-%02d [EURUSD TWO-WAY]", year, mon));
   WriteStatsLog("MONTHLY", D);
   WriteStatsLog("MONTHLY", StringFormat("Balance         : %10.2f USD", balance));
   WriteStatsLog("MONTHLY", StringFormat("PnL thang       : %+10.2f USD", gMthPnl));
   WriteStatsLog("MONTHLY", StringFormat("Cycles thang    : %10d  (W=%d L=%d WR=%.1f%%)", gMthCycles, gMthWin, gMthLoss, mthWR));
   WriteStatsLog("MONTHLY", StringFormat("Max DD thang    : %10.2f USD (%.1f%%)", gMthMaxDD, mthDD_pct));
   WriteStatsLog("MONTHLY", StringFormat("Net Withdrawn   : %+10.2f USD", gMthWithdrawn - gMthReplenished));
   WriteStatsLog("MONTHLY", StringFormat("Total PnL       : %+10.2f USD", gTotalPnl));
   WriteStatsLog("MONTHLY", StringFormat("Total Cycles    : %10d  (W=%d L=%d WR=%.1f%%)", gTotalCycles, gWinCycles, gLossCycles, allWR));
   WriteStatsLog("MONTHLY", D);
   Print(StringFormat("[NBE_TW] === MONTHLY REPORT %04d-%02d written ===", year, mon));
}

void WriteFinalStatsReport()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double allWR   = gTotalCycles > 0 ? (double)gWinCycles / gTotalCycles * 100.0 : 0.0;
   Print(StringFormat("[NBE_TW] === FINAL | Bal=%.2f Eq=%.2f | Cyc=%d WR=%.1f%% | Pnl=%+.2f | NetW=%+.2f ===",
         balance, equity, gTotalCycles, allWR, gTotalPnl, gTotalWithdrawn - gTotalReplenished));
}

void WriteStatsLog(string tag, string msg)
{
   MqlDateTime _dt; TimeToStruct(TimeCurrent(), _dt);
   int yr  = gCurYear  > 0 ? gCurYear  : _dt.year;
   int mon = gCurMonth > 0 ? gCurMonth : _dt.mon;
   int    flags;
   string fname;
   if(gIsTester)
   { fname = StringFormat("NBETW_STATS_%04d%02d.log", yr, mon); flags = FILE_WRITE|FILE_READ|FILE_TXT|FILE_ANSI; }
   else
   { fname = StringFormat("NBG\\NBETW_STATS_%04d%02d.log", yr, mon); flags = FILE_WRITE|FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI; }
   int handle = FileOpen(fname, flags, '\n');
   if(handle == INVALID_HANDLE) return;
   FileSeek(handle, 0, SEEK_END);
   FileWriteString(handle, StringFormat("[%s] [%s] %s\n", TimeToString(TimeCurrent()), tag, msg));
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| SAVE / LOAD STATE                                                |
//+------------------------------------------------------------------+
void SaveState()
{
   string p = "NBETW_";
   GlobalVariableSet(p+"Fund",      gWithdrawFund);
   GlobalVariableSet(p+"TotalW",    gTotalWithdrawn);
   GlobalVariableSet(p+"TotalR",    gTotalReplenished);
   GlobalVariableSet(p+"FundGtP",   gFundEverGtPrin?1.0:0.0);
   GlobalVariableSet(p+"TotalCyc",  gTotalCycles);
   GlobalVariableSet(p+"WinCyc",    gWinCycles);
   GlobalVariableSet(p+"LossCyc",   gLossCycles);
   GlobalVariableSet(p+"TotalPnl",  gTotalPnl);
   GlobalVariableSet(p+"LastDeal",  (double)gLastDealTicket);
   GlobalVariableSet(p+"LastWDay",  (double)gLastWithdrawDay);
   GlobalVariableSet(p+"LastWkC",   (double)gLastWeeklyClose);
   GlobalVariableSet(p+"LastRep",   (double)gLastReplenishWeek);
   GlobalVariableSet(p+"MthCyc",    gMthCycles);
   GlobalVariableSet(p+"MthWin",    gMthWin);
   GlobalVariableSet(p+"MthLoss",   gMthLoss);
   GlobalVariableSet(p+"MthPnl",    gMthPnl);
   GlobalVariableSet(p+"MthMaxDD",  gMthMaxDD);
   GlobalVariableSet(p+"MthHighEq", gMthHighEq);
   GlobalVariableSet(p+"MthW",      gMthWithdrawn);
   GlobalVariableSet(p+"MthR",      gMthReplenished);
   GlobalVariableSet(p+"MonW",      gMonthlyWithdrawn);
   GlobalVariableSet(p+"MonR",      gMonthlyReplenished);
   GlobalVariableSet(p+"CurMon",    gCurMonth);
   GlobalVariableSet(p+"CurYear",   gCurYear);
}

void LoadState()
{
   string p = "NBETW_";
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
