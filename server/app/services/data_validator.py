import asyncio
import datetime
import json
from typing import Dict, Any, List, Optional, Set
from sqlalchemy import text
from app.config import settings
from app.database import async_session
from app.security import get_fyers_token
from fyers_apiv3 import fyersModel

class ValidationProgress:
    def __init__(self):
        self.is_running = False
        self.total_symbols = 0
        self.current_index = 0
        self.current_symbol = ""
        self.errors: List[Dict[str, Any]] = []
        self.logs: List[str] = []
        self.started_at: Optional[datetime.datetime] = None
        self.completed_at: Optional[datetime.datetime] = None
        self.report: Dict[str, Any] = {}

    def reset(self, total_symbols: int):
        self.is_running = True
        self.total_symbols = total_symbols
        self.current_index = 0
        self.current_symbol = ""
        self.errors = []
        self.logs = []
        self.started_at = datetime.datetime.now(datetime.timezone.utc)
        self.completed_at = None
        self.report = {}
        self.log(f"Started data validation for {total_symbols} active instruments.")

    def log(self, message: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]

    def add_error(self, symbol: str, error_message: str):
        self.errors.append({
            "symbol": symbol,
            "error": error_message,
            "timestamp": datetime.datetime.now().isoformat()
        })
        self.log(f"ERROR ({symbol}): {error_message}")

    def complete(self, report: Dict[str, Any]):
        self.is_running = False
        self.completed_at = datetime.datetime.now(datetime.timezone.utc)
        self.report = report
        self.log("Validation completed successfully.")

    def cancel(self, reason: str):
        self.is_running = False
        self.completed_at = datetime.datetime.now(datetime.timezone.utc)
        self.log(f"Validation cancelled: {reason}")

validation_progress = ValidationProgress()

async def run_data_validation(years: int = 2):
    """
    Background validation task for market candle data in the PostgreSQL DB.
    """
    global validation_progress
    
    # Dates setup
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=365 * years)
    
    start_dt = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime.combine(today, datetime.time.max, tzinfo=datetime.timezone.utc)
    
    validation_progress.log("Fetching active Nifty 500 instruments from database...")
    
    # 1. Fetch symbols from DB
    async with async_session() as session:
        # Check active token first
        token_data = await get_fyers_token(session)
        access_token = None
        if token_data:
            expires_at = token_data["expires_at"]
            if expires_at > datetime.datetime.now(datetime.timezone.utc):
                access_token = token_data["access_token"]
        
        # Fetch Nifty 500 instruments
        query = text("""
            SELECT i.id, i.symbol, i.fyers_symbol, i.trading_symbol, i.name
            FROM instruments i
            JOIN universe_memberships m ON i.id = m.instrument_id
            WHERE m.universe_code = 'NIFTY500' AND m.member_to IS NULL AND i.active = true
            ORDER BY i.symbol ASC
        """)
        result = await session.execute(query)
        instruments = result.all()

    if not instruments:
        validation_progress.is_running = False
        validation_progress.log("Validation aborted: No active Nifty 500 instruments found in database.")
        return

    validation_progress.reset(len(instruments))
    
    # Initialize Fyers client if token is valid (for listing date probes)
    fyers = None
    if access_token:
        validation_progress.log("Fyers access token found. Active listing check enabled.")
        fyers = fyersModel.FyersModel(
            is_async=True,
            client_id=settings.fyers_app_id,
            token=access_token,
            log_path=""
        )
    else:
        validation_progress.log("WARNING: Fyers access token is missing or expired. Listing checking will assume first available candle as the listing date.")

    try:
        # 2. Fetch all daily candles in range in one query
        validation_progress.log(f"Loading daily candle data from {start_date} to {today}...")
        async with async_session() as session:
            candles_query = text("""
                SELECT instrument_id, candle_start, open_price, close_price, volume
                FROM market_candles
                WHERE timeframe = '1d' AND candle_start >= :start_dt AND candle_start <= :end_dt
                ORDER BY instrument_id, candle_start ASC
            """)
            candles_result = await session.execute(candles_query, {"start_dt": start_dt, "end_dt": end_dt})
            all_candles = candles_result.all()

        validation_progress.log(f"Loaded {len(all_candles)} daily candles. Building date mappings...")

        # Map instruments by ID
        inst_map = {inst.id: inst for inst in instruments}
        
        # Group candles by instrument_id
        candles_by_inst: Dict[Any, List[Any]] = {inst.id: [] for inst in instruments}
        for candle in all_candles:
            if candle.instrument_id in candles_by_inst:
                candles_by_inst[candle.instrument_id].append(candle)

        # 3. Determine Reference Trading Calendar
        # A date is a trading day if >= 50% of the active instruments have candles
        date_counts: Dict[datetime.date, int] = {}
        for candle in all_candles:
            c_date = candle.candle_start.date()
            date_counts[c_date] = date_counts.get(c_date, 0) + 1
            
        total_active_inst = len(instruments)
        ref_trading_days = sorted([
            d for d, count in date_counts.items()
            if count >= max(5, total_active_inst * 0.5)
        ])
        
        validation_progress.log(f"Inferred {len(ref_trading_days)} reference trading days from database.")

        # 4. Run Checks
        coverage_missing = []
        completeness_gaps = []
        anomalies = []
        
        # Spot check corporate actions
        spot_checks = {
            "RELIANCE": {
                "symbol": "RELIANCE",
                "ex_date": datetime.date(2024, 10, 28),
                "prev_date": datetime.date(2024, 10, 25),
                "ratio": 2.0, # 1:1 bonus
                "status": "Not Checked",
                "details": ""
            },
            "MAZDOCK": {
                "symbol": "MAZDOCK",
                "ex_date": datetime.date(2024, 12, 27),
                "prev_date": datetime.date(2024, 12, 26),
                "ratio": 2.0, # 1:2 split
                "status": "Not Checked",
                "details": ""
            }
        }

        # Spot checks verification logic helper
        def run_spot_check(sym: str, candles_list: List[Any]):
            if sym not in spot_checks:
                return
            chk = spot_checks[sym]
            ex_date = chk["ex_date"]
            prev_date = chk["prev_date"]
            
            pre_candle = None
            post_candle = None
            for c in candles_list:
                c_date = c.candle_start.date()
                if c_date == prev_date:
                    pre_candle = c
                elif c_date == ex_date:
                    post_candle = c
            
            if pre_candle and post_candle:
                pre_close = float(pre_candle.close_price)
                post_close = float(post_candle.close_price)
                ratio = pre_close / post_close
                # If pre-adjusted by Fyers, ratio is close to 1
                if 0.85 <= ratio <= 1.15:
                    chk["status"] = "PASSED"
                    chk["details"] = f"Adjusted (Pre-ex: {pre_close:.2f}, Post-ex: {post_close:.2f}, Ratio: {ratio:.2f})"
                # If unadjusted, ratio is close to chk["ratio"] (e.g. 2.0)
                elif (chk["ratio"] * 0.85) <= ratio <= (chk["ratio"] * 1.15):
                    chk["status"] = "FAILED"
                    chk["details"] = f"Cliff Detected! Unadjusted (Pre-ex: {pre_close:.2f}, Post-ex: {post_close:.2f}, Ratio: {ratio:.2f})"
                else:
                    chk["status"] = "SUSPICIOUS"
                    chk["details"] = f"Unexpected ratio (Pre-ex: {pre_close:.2f}, Post-ex: {post_close:.2f}, Ratio: {ratio:.2f})"
            else:
                chk["status"] = "MISSING_DATA"
                chk["details"] = f"Could not find candles for {prev_date} and/or {ex_date}."

        for idx, inst in enumerate(instruments, start=1):
            if not validation_progress.is_running:
                validation_progress.log("Validation interrupted.")
                break
                
            validation_progress.current_index = idx
            validation_progress.current_symbol = inst.symbol
            
            inst_candles = candles_by_inst.get(inst.id, [])
            
            # --- COVERAGE CHECK ---
            if not inst_candles:
                coverage_missing.append({
                    "symbol": inst.symbol,
                    "name": inst.name,
                    "fyers_symbol": inst.fyers_symbol
                })
                validation_progress.log(f"Symbol {inst.symbol} has 0 candles in target range.")
                continue

            # Run Spot Check if relevant
            if inst.symbol in spot_checks:
                run_spot_check(inst.symbol, inst_candles)

            # Sort candles just in case
            inst_candles = sorted(inst_candles, key=lambda c: c.candle_start)
            first_candle_date = inst_candles[0].candle_start.date()
            
            # --- DYNAMIC LISTING PROBE ---
            # If first candle is > 5 trading days after start_date, check if it was listed recently or if it's a gap
            expected_start_date = start_date
            is_recent_listing = False
            
            # Let's count how many trading days are before first_candle_date
            ref_days_before = [d for d in ref_trading_days if d < first_candle_date]
            if len(ref_days_before) > 5:
                # Stock starts late. Let's see if we have Fyers API to check if it's a recent listing
                if fyers:
                    probe_from = start_date.strftime("%Y-%m-%d")
                    probe_to = (first_candle_date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                    
                    data = {
                        "symbol": inst.fyers_symbol,
                        "resolution": "D",
                        "date_format": "1",
                        "range_from": probe_from,
                        "range_to": probe_to,
                        "cont_flag": "1"
                    }
                    
                    try:
                        # Fyers API call to probe historical data
                        response = await fyers.history(data=data)
                        if response.get("s") == "ok" and response.get("candles"):
                            # Fyers returns candles for this period! It means we have a silent gap in our DB
                            validation_progress.log(f"Silent gap probe: {inst.symbol} has {len(response['candles'])} candles on Fyers before its first local candle ({first_candle_date}). Flagging as silent gap.")
                        else:
                            # Fyers returns no data. This stock was indeed listed recently
                            is_recent_listing = True
                            expected_start_date = first_candle_date
                    except Exception as e:
                        validation_progress.add_error(inst.symbol, f"Listing probe failed: {str(e)}")
                        # Default to target start date to be conservative
                    
                    # Throttle Fyers API requests during validation (0.15s)
                    await asyncio.sleep(0.15)
                else:
                    # No Fyers API, assume recent listing but log it
                    is_recent_listing = True
                    expected_start_date = first_candle_date
            
            # --- COMPLETENESS CHECK (GAPS) ---
            actual_dates = {c.candle_start.date() for c in inst_candles}
            expected_dates = [d for d in ref_trading_days if d >= expected_start_date]
            
            missing_dates = sorted(list(set(expected_dates) - actual_dates))
            if missing_dates:
                completeness_gaps.append({
                    "symbol": inst.symbol,
                    "is_recent_listing": is_recent_listing,
                    "first_candle": first_candle_date.strftime("%Y-%m-%d"),
                    "gap_count": len(missing_dates),
                    "gaps": [d.strftime("%Y-%m-%d") for d in missing_dates[:15]], # limit to first 15 to keep payload clean
                    "has_more_gaps": len(missing_dates) > 15
                })

            # --- ANOMALY / CORPORATE ACTION SCAN (Drops > 35%) ---
            for i in range(1, len(inst_candles)):
                prev_c = inst_candles[i - 1]
                curr_c = inst_candles[i]
                
                prev_close = float(prev_c.close_price)
                curr_close = float(curr_c.close_price)
                
                if prev_close > 0:
                    drop = (prev_close - curr_close) / prev_close
                    if drop > 0.35: # drop of more than 35%
                        anomalies.append({
                            "symbol": inst.symbol,
                            "date": curr_c.candle_start.date().strftime("%Y-%m-%d"),
                            "prev_close": prev_close,
                            "close": curr_close,
                            "pct_drop": round(drop * 100, 2)
                        })
                        validation_progress.log(f"ANOMALY ({inst.symbol}): Drop of {drop*100:.2f}% on {curr_c.candle_start.date()}. Possible unadjusted corporate action.")

        # Construct final report
        report = {
            "validation_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "years_checked": years,
            "total_instruments_checked": len(instruments),
            "ref_trading_days_count": len(ref_trading_days),
            "coverage_check": {
                "passed": len(coverage_missing) == 0,
                "missing_count": len(coverage_missing),
                "missing_symbols": coverage_missing
            },
            "completeness_check": {
                "passed": len(completeness_gaps) == 0,
                "gaps_count": len(completeness_gaps),
                "symbols_with_gaps": completeness_gaps
            },
            "corporate_actions": {
                "spot_checks": [
                    {
                        "symbol": v["symbol"],
                        "ex_date": v["ex_date"].strftime("%Y-%m-%d"),
                        "status": v["status"],
                        "details": v["details"]
                    }
                    for v in spot_checks.values()
                ],
                "anomalies_found_count": len(anomalies),
                "anomalies": anomalies
            }
        }
        
        validation_progress.complete(report)
        
    except Exception as err:
        validation_progress.cancel(f"Validation crashed: {str(err)}")
    finally:
        if fyers:
            try:
                await fyers.close()
            except Exception:
                pass
