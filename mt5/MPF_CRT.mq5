//+------------------------------------------------------------------+
//| MPF_CRT.mq5 — Monsieur Pierre's Forex, C3 entry model            |
//|                                                                  |
//| A port of MPF's backtest rules (rules/RULEBOOK.md §3, §5-§7) to  |
//| an Expert Advisor for the MT5 Strategy Tester. Every rule here   |
//| was confirmed by the strategy's author.                          |
//|                                                                  |
//|  1. Bias: weekly if-then on the last two CLOSED weeks; if the    |
//|     week gives none, daily on the last two CLOSED days.          |
//|  2. CRT on D1 or 4H, in the bias direction. 4H C3 only at        |
//|     01/05/09/13 New York. Candles are cut on the NY clock.       |
//|  3. Mark: 00:00 NY for a daily C3, the candle's open for 4H.     |
//|  4. PLP: the most recent "low that broke a high" (wick is        |
//|     enough), unswept, within 48 bars, on 1H (daily) / 15M (4H).  |
//|     No PLP -> a fresh PD array (4H OB/FVG for daily, 1H/15M for  |
//|     4H) formed before the mark.                                  |
//|  5. After the PLP is taken / the array is stabbed, inside C3:    |
//|     Model #1 on 15M (5M if a 1H PD array sits under the PLP) for |
//|     daily, 5M for 4H. Last down-close candle in the move up,     |
//|     armed by a close above its high.                             |
//|  6. Stop 2 pips beyond that candle. T1 >= 2R or no trade: market |
//|     if the current price gives 2R, else a limit at the candle's  |
//|     high if that does. Limit dies at C3's close or if T1 prints. |
//|  7. 60% off at T1 (CRT 50%), rest at T2 (CRT extreme). No BE.    |
//|     Anything still open after 24h is closed.                     |
//+------------------------------------------------------------------+
#property copyright "MPF"
#property version   "1.00"
#property description "MPF CRT C3 entry model. Run on any chart of the pair; it reads M5/M15/H1 itself."

#include <Trade/Trade.mqh>

//--- how the broker's server clock relates to New York
enum ENUM_SERVER_CLOCK
  {
   CLOCK_NY_CLOSE = 0,   // NY close (GMT+2/+3, server midnight = 17:00 NY)
   CLOCK_EU_DST   = 1,   // GMT+2 winter / GMT+3 summer on EU dates
   CLOCK_FIXED    = 2    // fixed offset from UTC (set ServerUTCOffset)
  };

input group "Clock"
input ENUM_SERVER_CLOCK ServerClock     = CLOCK_NY_CLOSE;
input int               ServerUTCOffset = 0;      // hours, only for CLOCK_FIXED

input group "What to trade"
input bool   TradeDailyCRT   = true;
input bool   Trade4HCRT      = true;
input bool   UsePDArrayEntry = true;   // enter off a fresh PD array when there is no PLP

input group "Rules (author's values)"
input double MinRR           = 2.0;    // T1 must be at least this many R
input double StopBufferPips  = 2.0;    // beyond the Model #1 candle
input int    LookbackBars    = 48;     // PLP and PD-array search
input double PdReachPips     = 20.0;   // 1H PD array "resting below" the PLP
input double T1ClosePercent  = 60.0;
input int    TimeoutHours    = 24;

input group "Money"
input double RiskPercent     = 1.0;    // of balance, per trade
input double FixedLots       = 0.0;    // > 0 overrides RiskPercent
input long   Magic           = 260913;

//--- ------------------------------------------------------------------ types
struct Bar  { datetime t; double o, h, l, c; };
struct Zone { double top, bottom; int k; bool ok; };

enum SetupState { S_IDLE, S_WAIT_ARM, S_WAIT_SWEEP, S_TRACK, S_DONE };

struct Setup
  {
   SetupState state;
   int        dir;        // +1 long, -1 short
   string     name;       // "D1" / "H4"
   double     eq, opp;    // T1, T2
   datetime   c3, arm, end;
   double     level;      // PLP price, or the PD array edge
   bool       viaPD;
   ENUM_TIMEFRAMES etf;   // entry timeframe
   datetime   lastBar;    // last entry-TF bar processed
   bool       swept, haveOrigin;
   double     oh, ol;     // origin (last opposite-close) candle
  };

Setup g_d1, g_h4;
long  g_lastD1Key = LONG_MIN, g_lastH4Key = LONG_MIN;
datetime g_lastM5 = 0;
double g_pip;
CTrade trade;

//--- pending limit orders we placed, so they can be cancelled by our rules
ulong    g_pTicket[]; datetime g_pExpiry[]; double g_pT1[]; int g_pDir[];
//--- positions that already had their T1 partial
ulong    g_partialDone[];

//--- the funnel: where CRTs drop out (printed at the end of a test)
int f_crts, f_offhours, f_bias, f_noplp, f_notreached, f_nom1, f_rr, f_unfilled, f_traded,
    f_viaPlp, f_viaPd, f_market, f_limit;

//+------------------------------------------------------------------+
//| Clock: everything is decided on New York wall-clock time          |
//+------------------------------------------------------------------+
datetime NthSunday(int year, int mon, int n)
  {
   MqlDateTime d; ZeroMemory(d); d.year = year; d.mon = mon; d.day = 1;
   datetime first = StructToTime(d); TimeToStruct(first, d);
   int firstSun = 1 + (7 - d.day_of_week) % 7;
   d.day = firstSun + 7 * (n - 1); d.hour = 0; d.min = 0; d.sec = 0;
   return StructToTime(d);
  }

datetime LastSunday(int year, int mon)
  {
   int nm = mon == 12 ? 1 : mon + 1, ny = mon == 12 ? year + 1 : year;
   MqlDateTime d; ZeroMemory(d); d.year = ny; d.mon = nm; d.day = 1;
   datetime t = StructToTime(d) - 86400;          // last day of `mon`
   TimeToStruct(t, d);
   return t - d.day_of_week * 86400;
  }

bool UsDst(datetime utc)
  {
   MqlDateTime d; TimeToStruct(utc, d);
   datetime start = NthSunday(d.year, 3, 2) + 7 * 3600;   // 02:00 EST
   datetime stop  = NthSunday(d.year, 11, 1) + 6 * 3600;  // 02:00 EDT
   return utc >= start && utc < stop;
  }

bool EuDst(datetime utc)
  {
   MqlDateTime d; TimeToStruct(utc, d);
   return utc >= LastSunday(d.year, 3) + 3600 && utc < LastSunday(d.year, 10) + 3600;
  }

//--- server time -> New York wall clock, as a datetime
datetime NY(datetime server)
  {
   if(ServerClock == CLOCK_NY_CLOSE)
      return server - 7 * 3600;
   datetime utc;
   if(ServerClock == CLOCK_EU_DST)
      utc = server - (EuDst(server - 2 * 3600) ? 3 : 2) * 3600;
   else
      utc = server - ServerUTCOffset * 3600;
   return utc - (UsDst(utc) ? 4 : 5) * 3600;
  }

int  NYHour(datetime server) { return (int)(((long)NY(server) % 86400) / 3600); }

//--- server time of the open of the NY-clock bucket (span 4H or 1D) holding `s`
datetime BucketStart(datetime s, int span) { return s - (datetime)(((long)NY(s) - 17 * 3600) % span); }

//--- buckets on the NY clock: the forex day/week open at 17:00 NY,
//--- 4H candles at 17/21/01/05/09/13 NY. Epoch day 0 was a Thursday.
long DayKey(datetime s)  { return (long)(NY(s) - 17 * 3600) / 86400; }
long H4Key(datetime s)   { return (long)(NY(s) - 17 * 3600) / 14400; }
long WeekKey(datetime s) { return ((long)(NY(s) - 17 * 3600) - 3 * 86400) / (7 * 86400); }

//+------------------------------------------------------------------+
//| Data: only bars that have CLOSED by `now`                          |
//+------------------------------------------------------------------+
int ClosedBars(ENUM_TIMEFRAMES tf, datetime now, int count, Bar &out[])
  {
   MqlRates r[];
   int n = CopyRates(_Symbol, tf, 0, count, r);
   ArrayResize(out, 0);
   if(n <= 0) return 0;
   int sec = PeriodSeconds(tf), m = 0;
   ArrayResize(out, n);
   for(int i = 0; i < n; i++)
     {
      if(r[i].time + sec > now) continue;
      out[m].t = r[i].time; out[m].o = r[i].open; out[m].h = r[i].high;
      out[m].l = r[i].low;  out[m].c = r[i].close; m++;
     }
   ArrayResize(out, m);
   return m;
  }

//--- aggregate closed H1 bars into NY-clock buckets. mode 0=H4, 1=D1, 2=W1.
//--- keys[] receives each bucket's key. A bucket is kept only once every
//--- hour in it has closed and a later bucket exists (no forming candle).
int Aggregate(const Bar &h1[], int n, int mode, datetime now, Bar &out[], long &keys[])
  {
   ArrayResize(out, 0); ArrayResize(keys, 0);
   long nowKey = mode == 0 ? H4Key(now) : mode == 1 ? DayKey(now) : WeekKey(now);
   int m = 0;
   long cur = LONG_MIN;
   for(int i = 0; i < n; i++)
     {
      long k = mode == 0 ? H4Key(h1[i].t) : mode == 1 ? DayKey(h1[i].t) : WeekKey(h1[i].t);
      if(k >= nowKey) break;                  // the bucket `now` is in is still forming
      if(k != cur)
        {
         m++; ArrayResize(out, m); ArrayResize(keys, m);
         out[m-1] = h1[i]; keys[m-1] = k; cur = k;
        }
      else
        {
         out[m-1].h = MathMax(out[m-1].h, h1[i].h);
         out[m-1].l = MathMin(out[m-1].l, h1[i].l);
         out[m-1].c = h1[i].c;
        }
     }
   return m;
  }

bool IsUp(const Bar &b)   { return b.c > b.o; }
bool IsDown(const Bar &b) { return b.c < b.o; }

//+------------------------------------------------------------------+
//| Bias — Rulebook §3.1/§3.2. Reads CLOSED bars only.                 |
//+------------------------------------------------------------------+
int WeeklyBias(const Bar &p, const Bar &c)
  {
   if(c.c < p.l) return -1;
   if(c.c > p.h) return  1;
   if(c.h > p.h) return -1;
   if(c.l < p.l) return  1;
   return 0;
  }

int DailyBias(const Bar &p, const Bar &c)
  {
   bool up = c.h > p.h && c.c <= p.h, dn = c.l < p.l && c.c >= p.l;
   if(up && dn)  return 0;
   if(c.c > p.h) return 1;
   if(c.c < p.l) return -1;
   if(up) return -1;
   if(dn) return  1;
   return 0;
  }

//+------------------------------------------------------------------+
//| PLP — Rulebook §6. b[] ends at the last bar closed before the mark |
//+------------------------------------------------------------------+
bool IsSwingHigh(const Bar &b[], int i) { return b[i].h > b[i-1].h && b[i].h > b[i-2].h && b[i].h > b[i+1].h && b[i].h > b[i+2].h; }
bool IsSwingLow(const Bar &b[], int i)  { return b[i].l < b[i-1].l && b[i].l < b[i-2].l && b[i].l < b[i+1].l && b[i].l < b[i+2].l; }

bool FindPLP(const Bar &b[], int n, int dir, double &price)
  {
   int asOf = n - 1, floorIdx = MathMax(2, asOf - LookbackBars);
   bool wantHigh = dir < 0;
   // newest first: the most recent qualifying swing wins
   for(int i = n - 3; i >= floorIdx; i--)
     {
      bool isS = wantHigh ? IsSwingHigh(b, i) : IsSwingLow(b, i);
      if(!isS) continue;
      double px = wantHigh ? b[i].h : b[i].l;

      // unswept by the mark (sweeps counted after confirmation, i+2)
      bool swept = false;
      for(int j = i + 3; j <= asOf; j++)
         if(wantHigh ? b[j].h > px : b[j].l < px) { swept = true; break; }
      if(swept) continue;

      // "a low that broke a high": the most recent opposite swing before it
      int ref = -1;
      for(int j = i - 1; j >= 2; j--)
         if(wantHigh ? IsSwingLow(b, j) : IsSwingHigh(b, j)) { ref = j; break; }
      if(ref < 0) continue;
      double refPx = wantHigh ? b[ref].l : b[ref].h;
      bool broke = false;
      for(int j = i + 1; j <= asOf; j++)
        {
         if(wantHigh) { if(b[j].h > px) break; if(b[j].l < refPx) { broke = true; break; } }
         else         { if(b[j].l < px) break; if(b[j].h > refPx) { broke = true; break; } }
        }
      if(!broke) continue;
      price = px;
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| PD arrays — FVG (§2.1) and order block (§2.2) on one timeframe.    |
//| b[] holds only bars closed before the mark.                        |
//| fresh=true: nothing traded into it since it became knowable, and   |
//| it formed within LookbackBars. Returns the nearest to price.       |
//+------------------------------------------------------------------+
void ConsiderZone(Zone &best, int dir, double top, double bottom, int k)
  {
   double edge = dir > 0 ? top : bottom;
   if(!best.ok || (dir > 0 ? edge > best.top : edge < best.bottom))
     { best.top = top; best.bottom = bottom; best.k = k; best.ok = true; }
  }

bool Fresh(const Bar &b[], int n, int k, int dir, double top, double bottom)
  {
   for(int j = k + 1; j < n; j++)
      if(dir > 0 ? b[j].l <= top : b[j].h >= bottom) return false;
   return true;
  }

void ScanPD(const Bar &b[], int n, int dir, bool fresh, Zone &best,
            double nearLo = 0, double nearHi = 0, bool useNear = false)
  {
   int from = fresh ? MathMax(2, n - LookbackBars) : 2;
   // FVGs: known when bar 3 (k) closes
   for(int k = from; k < n; k++)
     {
      if(WeekKey(b[k-2].t) != WeekKey(b[k].t)) continue;     // weekend gap
      double top, bot;
      if(dir > 0 && b[k].l > b[k-2].h)      { top = b[k].l;   bot = b[k-2].h; }
      else if(dir < 0 && b[k].h < b[k-2].l) { top = b[k-2].l; bot = b[k].h;   }
      else continue;
      if(useNear && !(dir > 0 ? (top >= nearLo && top <= nearHi) : (bot >= nearLo && bot <= nearHi))) continue;
      if(fresh && !Fresh(b, n, k, dir, top, bot)) continue;
      ConsiderZone(best, dir, top, bot, k);
     }
   // order blocks: known when the displacement bar (k) closes
   for(int k = MathMax(from, 20); k < n; k++)
     {
      double atr = 0;
      for(int j = k - 20; j < k; j++) atr += b[j].h - b[j].l;
      atr /= 20.0;
      if(atr <= 0 || (b[k].h - b[k].l) < 1.5 * atr) continue;
      if(WeekKey(b[k-1].t) != WeekKey(b[k].t)) continue;
      if(dir > 0 ? !IsUp(b[k]) : !IsDown(b[k])) continue;
      for(int back = 1; back <= 10 && k - back >= 0; back++)
        {
         int j = k - back;
         if(dir > 0 ? IsDown(b[j]) : IsUp(b[j]))
           {
            double top = b[j].h, bot = b[j].l;
            bool inReach = !useNear || (dir > 0 ? (top >= nearLo && top <= nearHi) : (bot >= nearLo && bot <= nearHi));
            if(inReach && (!fresh || Fresh(b, n, k, dir, top, bot)))
               ConsiderZone(best, dir, top, bot, k);
            break;
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| Lifecycle                                                          |
//+------------------------------------------------------------------+
int OnInit()
  {
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_pip = (d == 3 || d == 5) ? 10 * _Point : _Point;
   trade.SetExpertMagicNumber(Magic);
   g_d1.state = S_IDLE; g_d1.name = "D1";
   g_h4.state = S_IDLE; g_h4.name = "H4";
   PrintFormat("MPF CRT: pip=%.5f, clock=%s. Check: server %s is %s New York.",
               g_pip, EnumToString(ServerClock),
               TimeToString(TimeCurrent()), TimeToString(NY(TimeCurrent())));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   PrintFormat("MPF funnel | CRTs %d | 4H off-hours %d | against bias %d | no PLP/PD array %d | "
               "not reached in C3 %d | no Model#1 in C3 %d | under %.1fR %d | limit unfilled %d | TRADED %d",
               f_crts, f_offhours, f_bias, f_noplp, f_notreached, f_nom1, MinRR, f_rr, f_unfilled, f_traded);
   PrintFormat("MPF orders | via PLP %d, via PD array %d | at market %d, limit placed %d (TRADED counts limits only once filled)",
               f_viaPlp, f_viaPd, f_market, f_limit);
  }

void OnTick()
  {
   ManagePositions();

   datetime t = iTime(_Symbol, PERIOD_M5, 0);
   if(t == 0 || t == g_lastM5) return;
   g_lastM5 = t;                       // a 5m bar just closed; `t` is "now"

   ManagePendings(t);

   if(TradeDailyCRT) StepCRT(g_d1, PERIOD_D1, t);
   if(Trade4HCRT)    StepCRT(g_h4, PERIOD_H4, t);
  }

//+------------------------------------------------------------------+
//| One CRT timeframe, one closed 5m bar at a time                     |
//+------------------------------------------------------------------+
void StepCRT(Setup &s, ENUM_TIMEFRAMES crtTf, datetime now)
  {
   bool daily = crtTf == PERIOD_D1;
   long key = daily ? DayKey(now) : H4Key(now);
   long last = daily ? g_lastD1Key : g_lastH4Key;

   // finish the running C3 first: its last entry bar closes exactly now
   if(s.state == S_WAIT_SWEEP || s.state == S_TRACK) Track(s, now);

   if(key != last)
     {
      if(daily) g_lastD1Key = key; else g_lastH4Key = key;
      if(last != LONG_MIN) NewCandle(s, daily, now);
     }

   if(s.state == S_WAIT_ARM && now >= s.arm) Arm(s, daily);
   if(s.state == S_WAIT_SWEEP || s.state == S_TRACK) Track(s, now);
  }

//--- a new D1/4H candle opened: was the pair before it C1 + C2?
void NewCandle(Setup &s, bool daily, datetime now)
  {
   if(s.state != S_IDLE && s.state != S_DONE)
     {
      // the previous C3 ended without an entry
      if(s.state == S_WAIT_SWEEP) f_notreached++;
      else if(s.state == S_TRACK) f_nom1++;
     }
   s.state = S_IDLE;

   Bar h1[]; int n = ClosedBars(PERIOD_H1, now, 900, h1);
   if(n < 200) return;
   Bar c[]; long keys[];
   int m = Aggregate(h1, n, daily ? 1 : 0, now, c, keys);
   if(m < 2) return;
   Bar c1, c2;
   c1 = c[m-2]; c2 = c[m-1];

   if(!(c2.c >= c1.l && c2.c <= c1.h)) return;            // must close back inside
   int dir = 0;
   if(c2.h > c1.h && c2.l >= c1.l) dir = -1;
   else if(c2.l < c1.l && c2.h <= c1.h) dir = 1;
   if(dir == 0) return;

   f_crts++;
   datetime c3 = BucketStart(now, daily ? 86400 : 14400);  // C3: the candle that just opened
   if(!daily)
     {
      int hr = NYHour(c3);
      if(hr != 1 && hr != 5 && hr != 9 && hr != 13) { f_offhours++; return; }
     }

   // bias from CLOSED weeks; fall back to CLOSED days
   Bar w[]; long wk[];
   int nw = Aggregate(h1, n, 2, now, w, wk);
   int bias = nw >= 2 ? WeeklyBias(w[nw-2], w[nw-1]) : 0;
   if(bias == 0)
     {
      Bar d[]; long dk[];
      int nd = Aggregate(h1, n, 1, now, d, dk);
      if(nd >= 2) bias = DailyBias(d[nd-2], d[nd-1]);
     }
   if(bias == 0 || bias != dir) { f_bias++; return; }

   s.dir = dir;
   s.eq  = (c1.h + c1.l) / 2.0;
   s.opp = dir > 0 ? c1.h : c1.l;
   s.c3  = c3;
   s.arm = daily ? c3 + 7 * 3600 : c3;                      // 00:00 NY for a daily C3
   s.end = c3 + (daily ? 86400 : 14400);
   s.swept = false; s.haveOrigin = false; s.viaPD = false;
   s.state = S_WAIT_ARM;
  }

//--- at the mark: find the PLP (or a fresh PD array) and the entry timeframe
void Arm(Setup &s, bool daily)
  {
   ENUM_TIMEFRAMES plpTf = daily ? PERIOD_H1 : PERIOD_M15;
   Bar pb[]; int n = ClosedBars(plpTf, s.arm, 400, pb);
   double plp = 0;
   if(n > LookbackBars + 10 && FindPLP(pb, n, s.dir, plp))
     {
      s.level = plp; s.viaPD = false;
      s.etf = daily ? PERIOD_M15 : PERIOD_M5;
      if(daily)
        {
         // a 1H PD array resting just beyond the PLP -> drop to the 5M
         Zone z; ZeroMemory(z);
         double reach = PdReachPips * g_pip;
         if(s.dir > 0) ScanPD(pb, n, s.dir, false, z, plp - reach, plp, true);
         else          ScanPD(pb, n, s.dir, false, z, plp, plp + reach, true);
         if(z.ok) s.etf = PERIOD_M5;
        }
     }
   else if(UsePDArrayEntry)
     {
      Zone z; ZeroMemory(z);
      if(daily)
        {
         Bar h1[]; int nh = ClosedBars(PERIOD_H1, s.arm, 900, h1);
         Bar h4[]; long k[];
         int m = Aggregate(h1, nh, 0, s.arm, h4, k);
         if(m > 30) ScanPD(h4, m, s.dir, true, z);
        }
      else
        {
         Bar h1[]; int nh = ClosedBars(PERIOD_H1, s.arm, 200, h1);
         if(nh > 30) ScanPD(h1, nh, s.dir, true, z);
         Bar m15[]; int nm = ClosedBars(PERIOD_M15, s.arm, 200, m15);
         if(nm > 30) ScanPD(m15, nm, s.dir, true, z);
        }
      if(!z.ok) { f_noplp++; s.state = S_DONE; return; }
      s.level = s.dir > 0 ? z.top : z.bottom;
      s.viaPD = true;
      s.etf = daily ? PERIOD_M15 : PERIOD_M5;
     }
   else { f_noplp++; s.state = S_DONE; return; }

   s.lastBar = s.arm - 1;
   s.state = S_WAIT_SWEEP;
  }

//--- walk the closed entry-timeframe bars: sweep / stab, then Model #1
void Track(Setup &s, datetime now)
  {
   Bar e[]; int n = ClosedBars(s.etf, now, 300, e);
   int sec = PeriodSeconds(s.etf);
   for(int i = 0; i < n; i++)
     {
      if(e[i].t <= s.lastBar || e[i].t < s.arm) continue;
      s.lastBar = e[i].t;
      if(e[i].t + sec > s.end)
        {
         if(s.state == S_WAIT_SWEEP) f_notreached++; else f_nom1++;
         s.state = S_DONE; return;
        }
      if(!s.swept)
        {
         bool hit = s.viaPD ? (s.dir > 0 ? e[i].l <= s.level : e[i].h >= s.level)
                            : (s.dir > 0 ? e[i].l <  s.level : e[i].h >  s.level);
         if(!hit) continue;
         s.swept = true; s.state = S_TRACK;
        }
      if(s.haveOrigin && (s.dir > 0 ? e[i].c > s.oh : e[i].c < s.ol))
        {
         Trigger(s);
         s.state = S_DONE;
         return;
        }
      if(s.dir > 0 ? IsDown(e[i]) : IsUp(e[i]))
        { s.haveOrigin = true; s.oh = e[i].h; s.ol = e[i].l; }
     }
  }

double RR(double entry, double stop, double target, int dir)
  {
   double risk = dir > 0 ? entry - stop : stop - entry;
   double rew  = dir > 0 ? target - entry : entry - target;
   return risk > 0 ? NormalizeDouble(rew / risk, 6) : 0;
  }

//--- Model #1 armed: market if the price now gives 2R, else limit at the candle
void Trigger(Setup &s)
  {
   double stop  = NormalizeDouble(s.dir > 0 ? s.ol - StopBufferPips * g_pip : s.oh + StopBufferPips * g_pip, _Digits);
   double limit = NormalizeDouble(s.dir > 0 ? s.oh : s.ol, _Digits);
   double tp    = NormalizeDouble(s.opp, _Digits);
   double px    = s.dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   string cmt   = StringFormat("MPF %s %s %.5f", s.name, s.viaPD ? "PD" : "PLP", s.eq);

   bool beyondLimit = s.dir > 0 ? px <= limit : px >= limit;   // already at/through it: market is better
   if(RR(px, stop, s.eq, s.dir) >= MinRR)
     {
      double lots = Lots(MathAbs(px - stop));
      bool ok = s.dir > 0 ? trade.Buy(lots, _Symbol, 0, stop, tp, cmt)
                          : trade.Sell(lots, _Symbol, 0, stop, tp, cmt);
      if(ok) { f_traded++; f_market++; if(s.viaPD) f_viaPd++; else f_viaPlp++; }
      else PrintFormat("MPF order failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return;
     }
   if(RR(limit, stop, s.eq, s.dir) >= MinRR && !beyondLimit)
     {
      double lots = Lots(MathAbs(limit - stop));
      bool ok = s.dir > 0 ? trade.BuyLimit(lots, limit, _Symbol, stop, tp, ORDER_TIME_GTC, 0, cmt)
                          : trade.SellLimit(lots, limit, _Symbol, stop, tp, ORDER_TIME_GTC, 0, cmt);
      if(ok)
        {
         f_limit++;
         int k = ArraySize(g_pTicket);
         ArrayResize(g_pTicket, k + 1); ArrayResize(g_pExpiry, k + 1);
         ArrayResize(g_pT1, k + 1);     ArrayResize(g_pDir, k + 1);
         g_pTicket[k] = trade.ResultOrder(); g_pExpiry[k] = s.end;
         g_pT1[k] = s.eq; g_pDir[k] = s.dir;
         if(s.viaPD) f_viaPd++; else f_viaPlp++;
        }
      else PrintFormat("MPF limit failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return;
     }
   f_rr++;
  }

double Lots(double stopDist)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lots = FixedLots;
   if(lots <= 0)
     {
      double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double perLot = stopDist / ts * tv;
      if(perLot <= 0) return vmin;
      lots = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0 / perLot;
     }
   lots = MathFloor(lots / step) * step;
   return MathMax(vmin, MathMin(vmax, lots));
  }

//+------------------------------------------------------------------+
//| Management                                                         |
//+------------------------------------------------------------------+
bool PartialDone(ulong ticket)
  {
   for(int i = 0; i < ArraySize(g_partialDone); i++) if(g_partialDone[i] == ticket) return true;
   return false;
  }

void ManagePositions()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != Magic || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      long type = PositionGetInteger(POSITION_TYPE);
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

      if(TimeCurrent() - opened >= TimeoutHours * 3600)
        { trade.PositionClose(tk); continue; }

      if(PartialDone(tk)) continue;
      // T1 is carried in the comment, so a restart does not lose it
      string parts[];
      if(StringSplit(PositionGetString(POSITION_COMMENT), ' ', parts) < 4) continue;
      double t1 = StringToDouble(parts[3]);
      if(t1 <= 0) continue;
      bool hit = type == POSITION_TYPE_BUY ? SymbolInfoDouble(_Symbol, SYMBOL_BID) >= t1
                                           : SymbolInfoDouble(_Symbol, SYMBOL_ASK) <= t1;
      if(!hit) continue;
      double vol  = PositionGetDouble(POSITION_VOLUME);
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double part = MathFloor(vol * T1ClosePercent / 100.0 / step) * step;
      int k = ArraySize(g_partialDone); ArrayResize(g_partialDone, k + 1); g_partialDone[k] = tk;
      if(part >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN) && part < vol)
         trade.PositionClosePartial(tk, part);
      else
         trade.PositionClose(tk);            // too small to split: all out at T1
     }
  }

//--- a resting limit dies when C3 closes, or if T1 prints before it fills
void ManagePendings(datetime now)
  {
   double hi = iHigh(_Symbol, PERIOD_M5, 1), lo = iLow(_Symbol, PERIOD_M5, 1);
   for(int i = ArraySize(g_pTicket) - 1; i >= 0; i--)
     {
      bool alive = OrderSelect(g_pTicket[i]);
      bool kill = alive && (now >= g_pExpiry[i] || (g_pDir[i] > 0 ? hi >= g_pT1[i] : lo <= g_pT1[i]));
      if(kill) { trade.OrderDelete(g_pTicket[i]); f_unfilled++; }
      if(!alive || kill)
        {
         // no longer pending and not killed by us: it filled
         if(!alive && HistoryOrderSelect(g_pTicket[i]) &&
            HistoryOrderGetInteger(g_pTicket[i], ORDER_STATE) == ORDER_STATE_FILLED)
            f_traded++;
         int last = ArraySize(g_pTicket) - 1;
         g_pTicket[i] = g_pTicket[last]; g_pExpiry[i] = g_pExpiry[last];
         g_pT1[i] = g_pT1[last]; g_pDir[i] = g_pDir[last];
         ArrayResize(g_pTicket, last); ArrayResize(g_pExpiry, last);
         ArrayResize(g_pT1, last); ArrayResize(g_pDir, last);
        }
     }
  }
//+------------------------------------------------------------------+
