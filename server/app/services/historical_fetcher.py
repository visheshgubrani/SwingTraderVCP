import asyncio
import datetime
import json
from typing import Dict, Any, List, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import async_session
from app.security import get_fyers_token
from fyers_apiv3 import fyersModel

class SyncProgress:
    def __init__(self):
        self.is_running = False
        self.total_symbols = 0
        self.current_index = 0
        self.current_symbol = ""
        self.errors: List[Dict[str, Any]] = []
        self.logs: List[str] = []
        self.started_at: Optional[datetime.datetime] = None
        self.completed_at: Optional[datetime.datetime] = None

    def reset(self, total_symbols: int):
        self.is_running = True
        self.total_symbols = total_symbols
        self.current_index = 0
        self.current_symbol = ""
        self.errors = []
        self.logs = []
        self.started_at = datetime.datetime.now(datetime.timezone.utc)
        self.completed_at = None
        self.log(f"Started sync of {total_symbols} symbols.")

    def log(self, message: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        # Keep only the last 300 logs
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]

    def add_error(self, symbol: str, error_message: str):
        self.errors.append({
            "symbol": symbol,
            "error": error_message,
            "timestamp": datetime.datetime.now().isoformat()
        })
        self.log(f"ERROR ({symbol}): {error_message}")

    def complete(self):
        self.is_running = False
        self.completed_at = datetime.datetime.now(datetime.timezone.utc)
        self.log(f"Sync completed. Successful: {self.current_index - len(self.errors)}/{self.total_symbols}. Errors: {len(self.errors)}.")

    def cancel(self, reason: str):
        self.is_running = False
        self.completed_at = datetime.datetime.now(datetime.timezone.utc)
        self.log(f"Sync cancelled: {reason}")

# Global singleton to hold current sync progress
sync_progress = SyncProgress()

async def run_historical_sync(years: int = 1):
    """
    Background worker task to fetch and save daily historical candles for the Nifty 500.
    """
    global sync_progress
    
    # Calculate dates
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=365 * years)
    
    # 1. Fetch symbols from DB
    async with async_session() as session:
        # Check active token first
        token_data = await get_fyers_token(session)
        if not token_data:
            sync_progress.is_running = False
            sync_progress.log("Sync failed: No active Fyers token in database. Please authenticate.")
            return

        access_token = token_data["access_token"]
        expires_at = token_data["expires_at"]
        if expires_at < datetime.datetime.now(datetime.timezone.utc):
            sync_progress.is_running = False
            sync_progress.log("Sync failed: Fyers token is expired. Please re-authenticate.")
            return

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
        sync_progress.is_running = False
        sync_progress.log("Sync aborted: No active Nifty 500 instruments found in database.")
        return

    # Initialize progress
    sync_progress.reset(len(instruments))
    
    # Initialize Fyers client in async mode
    fyers = fyersModel.FyersModel(
        is_async=True,
        client_id=settings.fyers_app_id,
        token=access_token,
        log_path=""
    )

    upsert_query = text("""
        INSERT INTO market_candles (
            instrument_id,
            timeframe,
            candle_start,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            source,
            raw_payload
        )
        VALUES (
            :instrument_id,
            '1d',
            :candle_start,
            :open_price,
            :high_price,
            :low_price,
            :close_price,
            :volume,
            'fyers',
            CAST(:raw_payload AS jsonb)
        )
        ON CONFLICT (instrument_id, timeframe, candle_start) DO UPDATE SET
            open_price = EXCLUDED.open_price,
            high_price = EXCLUDED.high_price,
            low_price = EXCLUDED.low_price,
            close_price = EXCLUDED.close_price,
            volume = EXCLUDED.volume,
            fetched_at = now(),
            raw_payload = EXCLUDED.raw_payload
    """)

    try:
        for idx, inst in enumerate(instruments, start=1):
            if not sync_progress.is_running:
                sync_progress.log("Sync interrupted by user or system.")
                break
                
            sync_progress.current_index = idx
            sync_progress.current_symbol = inst.symbol
            
            # Divide historical range into chunks of maximum 365 days (Fyers limit is 366 days for "D")
            chunks = []
            current_start = start_date
            while current_start < today:
                current_end = min(current_start + datetime.timedelta(days=365), today)
                chunks.append((current_start.strftime("%Y-%m-%d"), current_end.strftime("%Y-%m-%d")))
                current_start = current_end + datetime.timedelta(days=1)
            
            sync_progress.log(f"Syncing {inst.symbol} in {len(chunks)} chunks...")
            
            for chunk_from, chunk_to in chunks:
                if not sync_progress.is_running:
                    break
                
                # Fetch candle data for this chunk
                data = {
                    "symbol": inst.fyers_symbol,
                    "resolution": "D",
                    "date_format": "1",
                    "range_from": chunk_from,
                    "range_to": chunk_to,
                    "cont_flag": "1"
                }
                
                try:
                    # Fyers API call
                    response = await fyers.history(data=data)
                    
                    if response.get("s") != "ok":
                        error_msg = response.get("message", "API response error")
                        sync_progress.add_error(inst.symbol, f"Chunk {chunk_from} to {chunk_to} failed: {error_msg}")
                    else:
                        candles = response.get("candles", [])
                        sync_progress.log(f"Received {len(candles)} candles for {inst.symbol} ({chunk_from} to {chunk_to}).")
                        
                        if candles:
                            candle_dicts = []
                            for c in candles:
                                # Fyers returns epoch timestamp in seconds as float or int
                                epoch = int(c[0])
                                candle_start = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
                                
                                candle_dicts.append({
                                    "instrument_id": inst.id,
                                    "candle_start": candle_start,
                                    "open_price": float(c[1]),
                                    "high_price": float(c[2]),
                                    "low_price": float(c[3]),
                                    "close_price": float(c[4]),
                                    "volume": int(c[5]),
                                    "raw_payload": json.dumps({"c": c})
                                })
                                
                            # Batch insert into Postgres
                            async with async_session() as session:
                                await session.execute(upsert_query, candle_dicts)
                                await session.commit()
                                
                except Exception as e:
                    sync_progress.add_error(inst.symbol, f"Exception on chunk {chunk_from}-{chunk_to}: {str(e)}")
                    
                # Rate limiting sleep (0.35s yields ~170 requests per minute)
                await asyncio.sleep(0.35)
            
        sync_progress.complete()
        
    except Exception as general_err:
        sync_progress.cancel(f"Sync crashed: {str(general_err)}")
    finally:
        # Properly close async SDK resources
        try:
            await fyers.close()
        except Exception:
            pass
