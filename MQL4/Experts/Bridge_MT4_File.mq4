//+------------------------------------------------------------------+
//|  Bridge_MT4_File.mq4                                             |
//|  File-driven order executor for MetaTrader 4                     |
//|  Version: 1.0.0                                                  |
//|                                                                  |
//|  Reads key=value action files from BridgeFolder, executes        |
//|  trades, writes feedback to FeedbackFolder, archives processed   |
//|  files to ArchiveFolder.  No external dependencies.              |
//+------------------------------------------------------------------+
#property strict
#property copyright "Bridge EA"
#property version   "1.00"
#property description "Standalone file-driven bridge EA for MT4"
#include <WinUser32.mqh>

//+------------------------------------------------------------------+
//|  Input Parameters                                                |
//|                                                                  |
//|  Path rules — LOCAL SANDBOX MODE:                                |
//|  All paths are relative to MT4's local MQL4\Files\ folder.      |
//|  e.g. "bridge\\outgoing\\" resolves to:                         |
//|  ...\Terminal\<ID>\MQL4\Files\bridge\outgoing\                  |
//|  Create these subfolders inside MQL4\Files\ before attaching.   |
//|  Do NOT use FILE_COMMON — use local file mode only.             |
//+------------------------------------------------------------------+
extern string BridgeFolder        = "bridge\\outgoing\\";
extern string FeedbackFolder      = "bridge\\incoming\\";
extern string ArchiveFolder       = "bridge\\archive\\";
extern bool   OnlyCurrentSymbol   = false;
extern bool   AskForConfirmation  = false;
extern double MaxLotsPerTrade     = 1.0;
extern double MaxSpread           = 3.0;      // in points
extern int    MagicNumberBase     = 202600;
extern int    PollIntervalSeconds = 1;
extern int    Slippage            = 3;        // in points
extern bool   VerboseLogging      = true;
extern int    MaxFindFailures     = 10;       // alert after N consecutive FileFindFirst failures
extern int    MaxActionFileLines  = 50;       // max lines to read from one action file
extern int    MaxFileRetries      = 5;        // warn after N consecutive open failures per file

//+------------------------------------------------------------------+
//|  Enhancement 1 globals — FileFindFirst failure tracking         |
//+------------------------------------------------------------------+
int  g_findFailCount = 0;    // consecutive FileFindFirst failures
bool g_alertFired    = false; // true once alert has fired for this failure run

//+------------------------------------------------------------------+
//|  Enhancement 2 globals — per-file open retry tracking           |
//+------------------------------------------------------------------+
#define MAX_RETRY_ENTRIES 20
string g_retryFilenames[MAX_RETRY_ENTRIES];
int    g_retryCounts[MAX_RETRY_ENTRIES];
int    g_retryUsed = 0;      // number of active entries

//+------------------------------------------------------------------+
//|  RetryTrack                                                      |
//|  Enhancement 2: record a failed open attempt for a filename.    |
//|  Logs a warning after MaxFileRetries consecutive failures.      |
//+------------------------------------------------------------------+
void RetryTrack(string filename)
{
   // Search for existing entry
   for(int i = 0; i < g_retryUsed; i++)
   {
      if(g_retryFilenames[i] == filename)
      {
         g_retryCounts[i]++;
         if(g_retryCounts[i] >= MaxFileRetries)
            Print("WARNING: file '", filename, "' has failed to open ",
                  g_retryCounts[i], " times -- possible permanent lock or",
                  " permissions issue. Inspect manually.");
         return;
      }
   }
   // New entry — add if space available
   if(g_retryUsed < MAX_RETRY_ENTRIES)
   {
      g_retryFilenames[g_retryUsed] = filename;
      g_retryCounts[g_retryUsed]    = 1;
      g_retryUsed++;
   }
   else
   {
      Print("WARNING: RetryTracker full (", MAX_RETRY_ENTRIES,
            " entries) -- cannot track '", filename, "'");
   }
}

//+------------------------------------------------------------------+
//|  RetryClear                                                      |
//|  Enhancement 2: remove a filename from the retry tracker.       |
//|  Called when a file is successfully processed or disappears.    |
//+------------------------------------------------------------------+
void RetryClear(string filename)
{
   for(int i = 0; i < g_retryUsed; i++)
   {
      if(g_retryFilenames[i] == filename)
      {
         // Compact array — swap last entry into this slot
         g_retryFilenames[i] = g_retryFilenames[g_retryUsed - 1];
         g_retryCounts[i]    = g_retryCounts[g_retryUsed - 1];
         g_retryFilenames[g_retryUsed - 1] = "";
         g_retryCounts[g_retryUsed - 1]    = 0;
         g_retryUsed--;
         return;
      }
   }
}

//+------------------------------------------------------------------+
//|  ProbeFolder                                                     |
//|  Enhancement 4: Write and delete a probe file to verify a       |
//|  folder exists and is writable. Called from OnInit only.        |
//|  Logs a clear actionable error if the folder is not accessible. |
//+------------------------------------------------------------------+
bool ProbeFolder(string folder, string label)
{
   string probePath = folder + "_bridge_probe.tmp";
   int handle = FileOpen(probePath, FILE_WRITE | FILE_TXT);
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: Cannot write to ", label, " '", folder, "'");
      Print("       Folder may not exist or MT4 has no write access.");
      Print("       Create the folder and restart the EA.");
      return false;
   }
   FileWriteString(handle, "probe");
   FileClose(handle);
   FileDelete(probePath);
   if(VerboseLogging)
      Print("ProbeFolder: ", label, " OK — '", folder, "'");
   return true;
}

//+------------------------------------------------------------------+
//|  OnInit                                                          |
//+------------------------------------------------------------------+
int OnInit()
{
   // Warn if folder paths do not end with backslash
   if(StringLen(BridgeFolder)   == 0 || StringSubstr(BridgeFolder,   StringLen(BridgeFolder)   - 1) != "\\")
      Print("WARNING: BridgeFolder does not end with \\  — path: ", BridgeFolder);
   if(StringLen(FeedbackFolder) == 0 || StringSubstr(FeedbackFolder, StringLen(FeedbackFolder) - 1) != "\\")
      Print("WARNING: FeedbackFolder does not end with \\ — path: ", FeedbackFolder);
   if(StringLen(ArchiveFolder)  == 0 || StringSubstr(ArchiveFolder,  StringLen(ArchiveFolder)  - 1) != "\\")
      Print("WARNING: ArchiveFolder does not end with \\  — path: ", ArchiveFolder);

   // Log configured paths
   Print("Bridge_MT4_File initialised.");
   Print("  BridgeFolder   : ", BridgeFolder);
   Print("  FeedbackFolder : ", FeedbackFolder);
   Print("  ArchiveFolder  : ", ArchiveFolder);

   // Enhancement 4: Probe all three folders for write access at startup.
   // Logs a clear actionable error if any folder is missing or not writable.
   // EA continues running — operator can create the missing folder without
   // detaching and re-attaching the EA.
   ProbeFolder(BridgeFolder,   "BridgeFolder");
   ProbeFolder(FeedbackFolder, "FeedbackFolder");
   ProbeFolder(ArchiveFolder,  "ArchiveFolder");

   // Start poll timer
   EventSetTimer(PollIntervalSeconds);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//|  OnDeinit                                                        |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("Bridge_MT4_File deinitialized.");
}

//+------------------------------------------------------------------+
//|  StringTrim                                                      |
//|  Strip leading/trailing spaces and \r from a string.            |
//|  MQL4 has no built-in trim — this helper is required by all     |
//|  parsing functions.                                              |
//+------------------------------------------------------------------+
string StringTrim(string s)
{
   // Strip leading characters
   while(StringLen(s) > 0)
   {
      ushort ch = StringGetCharacter(s, 0);
      if(ch == ' ' || ch == '\r' || ch == '\t')
         s = StringSubstr(s, 1);
      else
         break;
   }

   // Strip trailing characters
   while(StringLen(s) > 0)
   {
      int    last = StringLen(s) - 1;
      ushort ch   = StringGetCharacter(s, last);
      if(ch == ' ' || ch == '\r' || ch == '\t')
         s = StringSubstr(s, 0, last);
      else
         break;
   }

   return s;
}

//+------------------------------------------------------------------+
//|  ReadActionFile                                                  |
//|  Parse a key=value action file into out-parameters.             |
//|  Returns true on success, false if file cannot be opened.       |
//|  On false: caller logs and skips without archiving.             |
//+------------------------------------------------------------------+
bool ReadActionFile(string path,
                    string &id,
                    string &asset,
                    string &action,
                    string &side,
                    double &size,
                    string &ordertype,
                    double &sl,
                    double &tp,
                    int    &magic,
                    string &valid_until,
                    string &comment)
{
   // Initialise all out-parameters to safe defaults
   id          = "";
   asset       = "";
   action      = "";
   side        = "";
   size        = 0.0;
   ordertype   = "";
   sl          = 0.0;
   tp          = 0.0;
   magic       = 0;
   valid_until = "";
   comment     = "";

   // Open file — FILE_COMMON required for absolute paths outside MT4 sandbox
   int handle = FileOpen(path, FILE_READ | FILE_TXT);
   if(handle == INVALID_HANDLE)
   {
      Print("ReadActionFile: cannot open file: ", path, " error: ", GetLastError());
      // Enhancement 2: track consecutive open failures per file
      RetryTrack(path);
      return false;
   }

   int lineCount = 0;

   // Read line by line
   while(!FileIsEnding(handle))
   {
      // Enhancement 3a: guard against oversized files (wrong file dropped in folder)
      lineCount++;
      if(lineCount > MaxActionFileLines)
      {
         Print("ReadActionFile: file exceeds MaxActionFileLines (", MaxActionFileLines,
               ") -- possible wrong file in BridgeFolder, skipping: ", path);
         FileClose(handle);
         return false;
      }

      string line = FileReadString(handle);
      line = StringTrim(line);

      // Skip blank lines
      if(StringLen(line) == 0)
         continue;

      // Split on first '=' — if fewer than 2 parts, skip silently
      // Note: StringSplit splits on every '=', so a value like "OB=TREND"
      // will produce 3 parts; we use only parts[0] and parts[1] (v1 limitation).
      string parts[];
      int count = StringSplit(line, StringGetCharacter("=", 0), parts);
      if(count < 2)
         continue;

      string key = StringTrim(parts[0]);
      string val = StringTrim(parts[1]);

      // Populate matching out-parameter; silently ignore unknown keys
      if(key == "id")               id          = val;
      else if(key == "asset")       asset       = val;
      else if(key == "action")      action      = val;
      else if(key == "side")        side        = val;
      else if(key == "size")        size        = StringToDouble(val);
      else if(key == "order_type")  ordertype   = val;
      else if(key == "sl")          sl          = StringToDouble(val);
      else if(key == "tp")          tp          = StringToDouble(val);
      else if(key == "magic_number")magic       = (int)StringToInteger(val);
      else if(key == "valid_until") valid_until = val;
      else if(key == "comment")     comment     = val;
      // unknown keys: fall through silently
   }

   FileClose(handle);
   return true;
}

//+------------------------------------------------------------------+
//|  WriteFeedback                                                   |
//|  Write a key=value feedback file to FeedbackFolder.             |
//|  Called after every action outcome — success or rejection.      |
//+------------------------------------------------------------------+
void WriteFeedback(string id,
                   string asset,
                   string action,
                   string status,
                   string side,
                   double size,
                   string tickets,
                   double avg_price,
                   string message,
                   int    error_code)
{
   string filepath = FeedbackFolder + id + "_result.txt";

   // FILE_COMMON required for absolute paths outside MT4 sandbox
   int handle = FileOpen(filepath, FILE_WRITE | FILE_TXT);
   if(handle == INVALID_HANDLE)
   {
      Print("WriteFeedback: cannot open feedback file: ", filepath,
            " error: ", GetLastError());
      return;
   }

   // Write all 10 fields in specified order
   FileWriteString(handle, "id="          + id                           + "\n");
   FileWriteString(handle, "status="      + status                       + "\n");
   FileWriteString(handle, "asset="       + asset                        + "\n");
   FileWriteString(handle, "action="      + action                       + "\n");
   FileWriteString(handle, "side="        + side                         + "\n");
   FileWriteString(handle, "size="        + DoubleToString(size, 2)      + "\n");
   FileWriteString(handle, "tickets="     + tickets                      + "\n");
   FileWriteString(handle, "avg_price="   + DoubleToString(avg_price, 5) + "\n");
   FileWriteString(handle, "message="     + message                      + "\n");
   FileWriteString(handle, "error_code="  + IntegerToString(error_code)  + "\n");

   FileClose(handle);

   if(VerboseLogging)
      Print("WriteFeedback: wrote ", filepath, " status=", status,
            " error_code=", error_code);
}

//+------------------------------------------------------------------+
//|  ArchiveActionFile                                               |
//|  Move a processed action file from BridgeFolder to             |
//|  ArchiveFolder. Falls back to FileDelete if move fails.         |
//|  Must be called before ProcessActionFile returns.               |
//+------------------------------------------------------------------+
void ArchiveActionFile(string path)
{
   // Extract filename only — find the last backslash and take everything after it
   string filename = path;
   int    len      = StringLen(path);
   for(int i = len - 1; i >= 0; i--)
   {
      if(StringGetCharacter(path, i) == '\\')
      {
         filename = StringSubstr(path, i + 1);
         break;
      }
   }

   string dest = ArchiveFolder + filename;

   // FILE_COMMON on both source and destination — both are absolute paths
   bool moved = FileMove(path, 0, dest, FILE_REWRITE);
   if(moved)
   {
      if(VerboseLogging)
         Print("ArchiveActionFile: moved ", filename, " -> ", dest);
   }
   else
   {
      Print("ArchiveActionFile: FileMove failed (error ", GetLastError(),
            "), deleting: ", path);
      bool deleted = FileDelete(path, 0);
      if(!deleted)
         Print("ArchiveActionFile: FileDelete also failed (error ",
               GetLastError(), ") -- file may remain in BridgeFolder: ", path);
   }
}

//+------------------------------------------------------------------+
//|  ValidateOpen                                                    |
//|  Tasks 3.3, 3.4, 3.5, 3.6 — lot size, spread, side, expiry.   |
//|  Returns true if all checks pass, false on first failure.       |
//|  Writes REJECTED feedback on failure; caller handles archive.   |
//+------------------------------------------------------------------+
bool ValidateOpen(string id, string asset, double size, string side,
                  string valid_until)
{
   // Enhancement 3b: reject zero or negative lot size
   // catches "size=abc" silently parsed as 0.0 by StringToDouble
   if(size <= 0.0)
   {
      Print("ValidateOpen: invalid lot size ", size, " (zero or negative)");
      WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                    "InvalidLotSize", 1);
      return false;
   }

   // --- Task 3.3: Lot size check ---
   if(size > MaxLotsPerTrade)
   {
      Print("ValidateOpen: lot size ", size, " exceeds MaxLotsPerTrade ",
            MaxLotsPerTrade);
      WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                    "LotSizeExceeded", 1);
      return false;
   }

   // --- Task 3.4: Spread check ---
   double spread_points = (Ask - Bid) / Point;
   if(spread_points > MaxSpread)
   {
      Print("ValidateOpen: spread ", spread_points, " pts exceeds MaxSpread ",
            MaxSpread);
      WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                    "MaxSpreadExceeded", 1);
      return false;
   }

   // --- Task 3.3 (side): side must be BUY or SELL ---
   if(side != "BUY" && side != "SELL")
   {
      Print("ValidateOpen: invalid side '", side, "'");
      WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                    "InvalidSide", 1);
      return false;
   }

   // --- Task 3.5: valid_until expiry check ---
   if(StringLen(valid_until) > 0)
   {
      // Expected ISO format: YYYY-MM-DDTHH:MM:SS
      // Parse using StringSubstr — positions are fixed in ISO 8601
      bool malformed = false;

      if(StringLen(valid_until) < 19)
      {
         malformed = true;
      }
      else
      {
         string s_year  = StringSubstr(valid_until, 0,  4);
         string s_month = StringSubstr(valid_until, 5,  2);
         string s_day   = StringSubstr(valid_until, 8,  2);
         string s_hour  = StringSubstr(valid_until, 11, 2);
         string s_min   = StringSubstr(valid_until, 14, 2);
         string s_sec   = StringSubstr(valid_until, 17, 2);

         int yr  = (int)StringToInteger(s_year);
         int mo  = (int)StringToInteger(s_month);
         int dy  = (int)StringToInteger(s_day);
         int hr  = (int)StringToInteger(s_hour);
         int mn  = (int)StringToInteger(s_min);
         int sc  = (int)StringToInteger(s_sec);

         // Basic sanity check on parsed values
         if(yr < 2000 || mo < 1 || mo > 12 || dy < 1 || dy > 31 ||
            hr < 0 || hr > 23 || mn < 0 || mn > 59 || sc < 0 || sc > 59)
         {
            malformed = true;
         }
         else
         {
            // Build datetime string in MQL4 format "YYYY.MM.DD HH:MM:SS"
            string dt_str = s_year + "." + s_month + "." + s_day + " " +
                            s_hour + ":" + s_min   + ":" + s_sec;
            datetime expiry = StringToTime(dt_str);

            // Compare with broker server time (TimeCurrent returns server time)
            // valid_until must be in broker server time — not UTC, not local time
            if(TimeCurrent() > expiry)
            {
               Print("ValidateOpen: action expired — valid_until=", valid_until,
                     " server_time=", TimeToString(TimeCurrent()));
               WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                             "ActionExpired", 1);
               return false;
            }
         }
      }

      if(malformed)
      {
         // Fail-open: log warning but treat as valid
         Print("ValidateOpen: WARNING malformed valid_until='", valid_until,
               "' — treating as valid (fail-open)");
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//|  ExecuteOpen                                                     |
//|  Tasks 4.1, 4.2 — confirmation prompt and market order send.   |
//+------------------------------------------------------------------+
void ExecuteOpen(string id, string asset, string side, double size,
                 double sl, double tp, int magic, string comment)
{
   // --- Task 4.1: AskForConfirmation prompt ---
   // MessageBox is a blocking Win32 dialog — MT4 UI freezes until user responds.
   // This is intentional behaviour, not a bug.
   if(AskForConfirmation)
   {
      string summary = "Asset: "  + asset + "\n" +
                       "Side:  "  + side  + "\n" +
                       "Size:  "  + DoubleToString(size, 2) + " lots\n" +
                       "SL:    "  + DoubleToString(sl, 5)   + "\n" +
                       "TP:    "  + DoubleToString(tp, 5)   + "\n" +
                       "Magic: "  + IntegerToString(magic);
      int response = MessageBox(summary, "Confirm Trade", MB_OKCANCEL);
      if(response == IDCANCEL)
      {
         Print("ExecuteOpen: user cancelled trade id=", id);
         WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                       "UserCancelled", 1);
         return;  // caller (ProcessActionFile) handles archive
      }
   }

   // --- Task 4.2: Market order execution ---

   // Step 2: Refresh rates before reading Ask/Bid
   RefreshRates();

   // Step 3 & 4: Determine order type and price
   // Use MarketInfo(asset, ...) — NOT the global Ask/Bid which are chart-symbol only.
   // This allows the EA to trade any symbol regardless of which chart it is attached to.
   int    op    = (side == "BUY") ? OP_BUY  : OP_SELL;
   double price = (side == "BUY") ? MarketInfo(asset, MODE_ASK)
                                  : MarketInfo(asset, MODE_BID);

   // Step 5: Determine magic number
   int finalMagic = (magic > 0) ? magic : MagicNumberBase;

   // Step 6: Send order
   int ticket = OrderSend(asset, op, size, price, Slippage, sl, tp,
                          comment, finalMagic, 0, clrNONE);

   // Step 7a: Success
   if(ticket > 0)
   {
      // OrderSelect is mandatory before reading order properties —
      // OrderSend does not guarantee the new order is selected.
      if(!OrderSelect(ticket, SELECT_BY_TICKET))
      {
         Print("ExecuteOpen: OrderSelect failed after OrderSend ticket=", ticket,
               " error=", GetLastError());
         // Still write FILLED — we have the ticket, price unknown
         WriteFeedback(id, asset, "OPEN", "FILLED", side, size,
                       IntegerToString(ticket), 0.0, "OrderSelectFailed", 0);
      }
      else
      {
         WriteFeedback(id, asset, "OPEN", "FILLED", side, size,
                       IntegerToString(ticket), OrderOpenPrice(), "", 0);
      }
   }
   // Step 7b: Failure
   else
   {
      int err = GetLastError();
      Print("ExecuteOpen: OrderSend failed error=", err,
            " asset=", asset, " side=", side, " size=", size);
      WriteFeedback(id, asset, "OPEN", "REJECTED", side, size, "", 0.0,
                    IntegerToString(err), 2);
   }
}

//+------------------------------------------------------------------+
//|  ExecuteCloseAll                                                 |
//|  Task 5.1 — close all matching open orders for a symbol.       |
//+------------------------------------------------------------------+
void ExecuteCloseAll(string id, string asset, int magic)
{
   // Determine target symbol — respects OnlyCurrentSymbol setting
   string resolvedSymbol = OnlyCurrentSymbol ? Symbol() : asset;

   string closedTickets = "";
   int    closedCount   = 0;
   int    failCount     = 0;

   // Iterate in reverse to avoid index shifting as orders are removed
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;

      // Filter by symbol
      if(OrderSymbol() != resolvedSymbol)
         continue;

      // Filter by magic number if specified
      if(magic > 0 && OrderMagicNumber() != magic)
         continue;

      // Determine close price: BUY closes at Bid, SELL closes at Ask
      double closePrice = (OrderType() == OP_BUY) ? Bid : Ask;
      int    ticket     = OrderTicket();
      double lots       = OrderLots();

      bool closed = OrderClose(ticket, lots, closePrice, Slippage, clrNONE);
      if(closed)
      {
         closedCount++;
         if(StringLen(closedTickets) > 0)
            closedTickets += ",";
         closedTickets += IntegerToString(ticket);
      }
      else
      {
         failCount++;
         Print("ExecuteCloseAll: order close failed, ticket: ", OrderTicket(),
               " error: ", GetLastError());
      }
   }

   if(closedCount > 0)
   {
      WriteFeedback(id, asset, "CLOSE_ALL", "FILLED", "", 0.0,
                    closedTickets, 0.0, "", 0);
   }
   else
   {
      WriteFeedback(id, asset, "CLOSE_ALL", "REJECTED", "", 0.0, "", 0.0,
                    "NoOrdersToClose", 1);
   }
}

//+------------------------------------------------------------------+
//|  ProcessActionFile                                               |
//|  Tasks 3.1, 3.2, 6.2 — orchestrates parse → validate →        |
//|  execute → feedback → archive for a single action file.        |
//|  Every branch calls ArchiveActionFile before returning.         |
//+------------------------------------------------------------------+
void ProcessActionFile(string path)
{
   string id, asset, action, side, ordertype, valid_until, comment;
   double size, sl, tp;
   int    magic;

   // --- Step 1: Parse ---
   // On failure: log and skip without archiving (retry next poll cycle)
   if(!ReadActionFile(path, id, asset, action, side, size,
                      ordertype, sl, tp, magic, valid_until, comment))
   {
      Print("ProcessActionFile: skipping unreadable file: ", path);
      return;
   }
   // Enhancement 2: file opened successfully — clear any retry record for this path
   RetryClear(path);

   if(VerboseLogging)
      Print("ProcessActionFile: parsed id=", id, " action=", action,
            " asset=", asset);

   // --- Task 3.1: Required field validation ---
   if(StringLen(id) == 0 || StringLen(action) == 0 || StringLen(asset) == 0)
   {
      Print("ProcessActionFile: missing required field(s) id='", id,
            "' action='", action, "' asset='", asset, "'");
      WriteFeedback(id, asset, action, "REJECTED", side, size, "", 0.0,
                    "MissingRequiredField", 1);
      ArchiveActionFile(path);
      return;
   }

   // --- Task 3.2: OnlyCurrentSymbol filter ---
   if(OnlyCurrentSymbol && asset != Symbol())
   {
      Print("ProcessActionFile: symbol mismatch asset=", asset,
            " chart=", Symbol());
      WriteFeedback(id, asset, action, "REJECTED", side, size, "", 0.0,
                    "SymbolMismatch", 1);
      ArchiveActionFile(path);
      return;
   }

   // --- Action dispatch ---
   if(action == "OPEN")
   {
      // ValidateOpen returns false and writes its own REJECTED feedback
      if(!ValidateOpen(id, asset, size, side, valid_until))
      {
         ArchiveActionFile(path);
         return;
      }
      ExecuteOpen(id, asset, side, size, sl, tp, magic, comment);
      ArchiveActionFile(path);
   }
   else if(action == "CLOSE_ALL")
   {
      ExecuteCloseAll(id, asset, magic);
      ArchiveActionFile(path);
   }
   else
   {
      Print("ProcessActionFile: unknown action '", action, "'");
      WriteFeedback(id, asset, action, "REJECTED", side, size, "", 0.0,
                    "UnknownAction", 1);
      ArchiveActionFile(path);
   }
}

//+------------------------------------------------------------------+
//|  OnTimer                                                         |
//|  Task 6.1 — poll BridgeFolder for action files on every tick.  |
//|  MT4 OnTimer is not re-entrant — queued ticks wait; no mutex   |
//|  needed.                                                         |
//|  Enhancement 1: tracks consecutive FileFindFirst failures and   |
//|  fires a one-shot Alert() after MaxFindFailures is reached.     |
//+------------------------------------------------------------------+
void OnTimer()
{
   string mask     = BridgeFolder + "*.txt";
   string filename = "";

   long findHandle = FileFindFirst(mask, filename, 0);
   if(findHandle == INVALID_HANDLE)
   {
      // Distinguish empty folder (GetLastError()==0) from real failure
      int err = GetLastError();
      if(err != 0)
      {
         // Real FileFindFirst failure — track it
         g_findFailCount++;
         Print("OnTimer: FileFindFirst failed (error ", err,
               ") consecutive count=", g_findFailCount);

         if(g_findFailCount >= MaxFindFailures && !g_alertFired)
         {
            Alert("Bridge EA: FileFindFirst has failed ", g_findFailCount,
                  " times consecutively. Check BridgeFolder path ('",
                  BridgeFolder, "') and MT4 build. EA is NOT polling.");
            g_alertFired = true;
         }
      }
      else
      {
         // Empty folder — valid, reset failure counter
         if(g_findFailCount > 0)
         {
            Print("OnTimer: FileFindFirst recovered after ", g_findFailCount,
                  " failure(s)");
            g_findFailCount = 0;
            g_alertFired    = false;
         }
      }
      return;
   }

   // Successful find — reset failure counter
   if(g_findFailCount > 0)
   {
      Print("OnTimer: FileFindFirst recovered after ", g_findFailCount,
            " failure(s)");
      g_findFailCount = 0;
      g_alertFired    = false;
   }

   do
   {
      string relpath = BridgeFolder + filename;
      if(VerboseLogging)
         Print("OnTimer: found file ", filename);
      ProcessActionFile(relpath);
   }
   while(FileFindNext(findHandle, filename));

   FileFindClose(findHandle);
}
