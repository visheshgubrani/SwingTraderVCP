import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple

def compute_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes technical indicators on a DataFrame of daily candles.
    The DataFrame MUST be sorted chronologically and contain:
    - 'close' (float)
    - 'volume' (float/int)
    
    Returns the DataFrame with additional columns:
    - 'sma_50'
    - 'sma_150'
    - 'sma_200'
    - 'sma_200_prev'
    - 'sma_200_prev_22'
    - 'sma_200_prev_110'
    - 'high_52w'
    - 'low_52w'
    - 'avg_volume_20'
    - 'pct_from_52w_high'
    """
    # Create copy to prevent modifying the input DataFrame
    df = df.copy()
    
    # Sort by date/candle_start if available to ensure correct calculation order
    if 'date' in df.columns:
        df = df.sort_values('date').reset_index(drop=True)
    elif 'candle_start' in df.columns:
        df = df.sort_values('candle_start').reset_index(drop=True)

    # Rolling calculations
    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['sma_150'] = df['close'].rolling(window=150).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    df['sma_200_prev'] = df['sma_200'].shift(1)
    
    # Lookbacks for 200 SMA trend: 1 month (~22 trading days), 5 months (~110 trading days)
    df['sma_200_prev_22'] = df['sma_200'].shift(22)
    df['sma_200_prev_110'] = df['sma_200'].shift(110)
    
    df['high_52w'] = df['close'].rolling(window=252).max()
    df['low_52w'] = df['close'].rolling(window=252).min()
    
    df['avg_volume_20'] = df['volume'].rolling(window=20).mean()
    df['pct_from_52w_high'] = (df['high_52w'] - df['close']) / df['high_52w']
    
    return df

def compute_weighted_performance_score(df: pd.DataFrame) -> float:
    """
    Computes a weighted performance score based on the Rate of Change (ROC)
    over 3-month (63 trading days), 6-month (126 trading days), 9-month (189 trading days),
    and 12-month (252 trading days) periods:
    Score = 0.4 * ROC_3M + 0.2 * ROC_6M + 0.2 * ROC_9M + 0.2 * ROC_12M
    """
    if len(df) < 253:
        return float('nan')
        
    latest_close = float(df.iloc[-1]['close'])
    
    # Safely get historical closes
    close_3m = float(df.iloc[-64]['close']) if len(df) >= 64 else float('nan')
    close_6m = float(df.iloc[-127]['close']) if len(df) >= 127 else float('nan')
    close_9m = float(df.iloc[-190]['close']) if len(df) >= 190 else float('nan')
    close_12m = float(df.iloc[-253]['close']) if len(df) >= 253 else float('nan')
    
    if any(pd.isna(v) for v in [latest_close, close_3m, close_6m, close_9m, close_12m]):
        return float('nan')
        
    roc_3m = (latest_close - close_3m) / close_3m
    roc_6m = (latest_close - close_6m) / close_6m
    roc_9m = (latest_close - close_9m) / close_9m
    roc_12m = (latest_close - close_12m) / close_12m
    
    score = (0.40 * roc_3m) + (0.20 * roc_6m) + (0.20 * roc_9m) + (0.20 * roc_12m)
    return score

def compute_relative_strength_ratings(stocks_data: list) -> dict:
    """
    Given a list of dicts/objects containing 'instrument_id' and 'perf_score',
    ranks them by 'perf_score' and assigns a percentile rank from 1 to 99.
    
    Returns a dictionary mapping instrument_id to rs_rating.
    """
    # Filter out records with NaN or invalid scores
    valid_stocks = [
        s for s in stocks_data 
        if s.get("perf_score") is not None and not pd.isna(s["perf_score"])
    ]
    
    if not valid_stocks:
        return {}
        
    # Sort by performance score ascending (so lower scores get lower ranks/percentiles)
    valid_stocks.sort(key=lambda x: x["perf_score"])
    
    total = len(valid_stocks)
    rs_ratings = {}
    
    for idx, s in enumerate(valid_stocks):
        inst_id = s["instrument_id"]
        # Percentile ranking mapped from 1 to 99
        rs_rating = int(round((idx + 1) / total * 98) + 1)
        # Clip to ensure bounds [1, 99]
        rs_rating = max(1, min(99, rs_rating))
        rs_ratings[inst_id] = rs_rating
        
    return rs_ratings

def evaluate_minervini_criteria(df: pd.DataFrame, rs_rating: int) -> Tuple[bool, Dict[str, Any]]:
    """
    Evaluates if the latest day in the indicators DataFrame meets the Minervini Trend Template:
    1. Moving Average Sequence: Price must be above both the 150-day and 200-day moving averages.
    2. Moving Average Alignment: The 150-day moving average must be above the 200-day moving average.
    3. Uptrend Duration: The 200-day moving average line must be trending upward for at least 1 month (~22 trading days).
    4. Short-Term Trend: The 50-day moving average must be above both the 150-day and 200-day moving averages.
    5. Price & 50 MA: The current stock price must be trading above the 50-day moving average.
    6. Price vs. 52-Week Low: The stock price must be at least 30% above its 52-week low.
    7. Price vs. 52-Week High: The stock price must be within 25% of its 52-week high.
    8. Relative Strength (RS): The stock’s Relative Strength (RS) rating should ideally be no less than 70.
    
    Returns (passed, metrics_dict)
    """
    if len(df) < 252:
        return False, {"error": f"Insufficient historical lookback. Have {len(df)} days, need at least 252 days."}
        
    latest = df.iloc[-1]
    
    # Extract values
    close = float(latest['close'])
    sma_50 = float(latest['sma_50'])
    sma_150 = float(latest['sma_150'])
    sma_200 = float(latest['sma_200'])
    sma_200_prev = float(latest['sma_200_prev'])
    sma_200_prev_22 = float(latest['sma_200_prev_22']) if 'sma_200_prev_22' in latest and not pd.isna(latest['sma_200_prev_22']) else None
    sma_200_prev_110 = float(latest['sma_200_prev_110']) if 'sma_200_prev_110' in latest and not pd.isna(latest['sma_200_prev_110']) else None
    high_52w = float(latest['high_52w'])
    low_52w = float(latest['low_52w'])
    avg_vol_20 = float(latest['avg_volume_20'])
    pct_from_52w_high = float(latest['pct_from_52w_high'])
    
    # Check for NaN in essential indicator columns
    if any(pd.isna(v) for v in [close, sma_50, sma_150, sma_200, high_52w, low_52w, avg_vol_20]):
        return False, {"error": "NaN values found in calculated indicators."}
        
    # Enforce conditions
    cond_ma_sequence = close > sma_150 and close > sma_200
    cond_ma_alignment = sma_150 > sma_200
    cond_uptrend_1m = (sma_200 > sma_200_prev_22) if sma_200_prev_22 is not None else False
    cond_short_term = sma_50 > sma_150 and sma_50 > sma_200
    cond_price_above_50 = close > sma_50
    cond_above_52w_low = close >= 1.30 * low_52w
    cond_near_52w_high = close >= 0.75 * high_52w
    cond_rs_rating = rs_rating >= 70
    
    passed = (
        cond_ma_sequence and 
        cond_ma_alignment and 
        cond_uptrend_1m and 
        cond_short_term and 
        cond_price_above_50 and 
        cond_above_52w_low and 
        cond_near_52w_high and 
        cond_rs_rating
    )
    
    # Preferred checks (not hard constraints, but logged)
    cond_uptrend_5m = (sma_200 > sma_200_prev_110) if sma_200_prev_110 is not None else False
    
    metrics = {
        "close": close,
        "sma_50": sma_50,
        "sma_150": sma_150,
        "sma_200": sma_200,
        "sma_200_yesterday": sma_200_prev,
        "sma_200_prev_22": sma_200_prev_22,
        "sma_200_prev_110": sma_200_prev_110,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "avg_volume_20": int(avg_vol_20),
        "pct_from_52w_high": pct_from_52w_high,
        "rs_rating": rs_rating,
        "criteria_matches": {
            "price_above_150_200_sma": bool(cond_ma_sequence),
            "sma_150_above_200_sma": bool(cond_ma_alignment),
            "sma_200_trending_up_1m": bool(cond_uptrend_1m),
            "sma_200_trending_up_5m_pref": bool(cond_uptrend_5m),
            "sma_50_above_150_200_sma": bool(cond_short_term),
            "price_above_50_sma": bool(cond_price_above_50),
            "above_30pct_52w_low": bool(cond_above_52w_low),
            "within_25pct_52w_high": bool(cond_near_52w_high),
            "rs_rating_above_threshold": bool(cond_rs_rating)
        }
    }
    
    return passed, metrics
