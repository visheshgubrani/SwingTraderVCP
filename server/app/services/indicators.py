import numpy as np
import pandas as pd
from typing import Any, Dict, Tuple

from app.services.screening_config import TechnicalScreeningConfig


def compute_technical_indicators(
    df: pd.DataFrame,
    config: TechnicalScreeningConfig | None = None,
) -> pd.DataFrame:
    """
    Computes technical indicators on a DataFrame of daily candles.
    The DataFrame MUST be sorted chronologically and contain:
    - 'high', 'low', 'close' (float)
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
    - liquidity, volatility-contraction, Bollinger-width, and volume-ratio fields
    """
    config = config or TechnicalScreeningConfig()

    required_columns = {"high", "low", "close", "volume"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing required OHLCV columns: {missing}")

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
    
    df['high_52w'] = df['high'].rolling(window=252).max()
    df['low_52w'] = df['low'].rolling(window=252).min()
    
    df['avg_volume_20'] = df['volume'].rolling(window=20).mean()
    df['pct_from_52w_high'] = (df['high_52w'] - df['close']) / df['high_52w']

    # Liquidity: average daily traded value (close * volume), expressed in INR crore.
    traded_value = df['close'] * df['volume']
    df['adtv_crore'] = (
        traded_value.rolling(window=config.liquidity_lookback_days).mean() / 10_000_000
    )

    # ATR contraction: Wilder ATR(10) / ATR(50), compared with its trailing 3-month low.
    previous_close = df['close'].shift(1)
    true_range = pd.concat(
        [
            df['high'] - df['low'],
            (df['high'] - previous_close).abs(),
            (df['low'] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df['atr_10'] = true_range.ewm(
        alpha=1 / config.atr_short_days,
        adjust=False,
        min_periods=config.atr_short_days,
    ).mean()
    df['atr_50'] = true_range.ewm(
        alpha=1 / config.atr_long_days,
        adjust=False,
        min_periods=config.atr_long_days,
    ).mean()
    df['atr_ratio'] = df['atr_10'] / df['atr_50']
    df['atr_ratio_3m_low'] = df['atr_ratio'].rolling(
        window=config.atr_low_lookback_days,
        min_periods=config.atr_low_lookback_days,
    ).min()

    # Bollinger width relative to the stock's own trailing six-month distribution.
    bb_middle = df['close'].rolling(window=config.bb_window_days).mean()
    bb_std = df['close'].rolling(window=config.bb_window_days).std(ddof=0)
    df['bb_width'] = (
        (2 * config.bb_std_deviations * bb_std) / bb_middle.replace(0, np.nan)
    )
    df['bb_width_20th_pct'] = df['bb_width'].rolling(
        window=config.bb_percentile_lookback_days,
        min_periods=config.bb_percentile_lookback_days,
    ).quantile(config.bb_reference_percentile)
    df['bb_width_percentile'] = df['bb_width'].rolling(
        window=config.bb_percentile_lookback_days,
        min_periods=config.bb_percentile_lookback_days,
    ).apply(
        lambda window: float(np.count_nonzero(window <= window[-1])) / len(window),
        raw=True,
    )

    # Volume dry-up into the pivot.
    df['avg_volume_10'] = df['volume'].rolling(window=config.volume_short_days).mean()
    df['avg_volume_50'] = df['volume'].rolling(window=config.volume_long_days).mean()
    df['volume_dry_up_ratio'] = (
        df['avg_volume_10'] / df['avg_volume_50'].replace(0, np.nan)
    )

    previous_close_for_dir = df['close'].shift(1)
    is_up_day = df['close'] > previous_close_for_dir
    is_down_day = df['close'] < previous_close_for_dir
    up_volume = df['volume'].where(is_up_day, 0.0)
    down_volume = df['volume'].where(is_down_day, 0.0)
    up_sum = up_volume.rolling(
        window=config.up_down_volume_lookback_days,
        min_periods=config.up_down_volume_lookback_days,
    ).sum()
    down_sum = down_volume.rolling(
        window=config.up_down_volume_lookback_days,
        min_periods=config.up_down_volume_lookback_days,
    ).sum()
    df['up_down_volume_ratio'] = up_sum / down_sum.replace(0, np.nan)

    df['pocket_pivot'] = _compute_pocket_pivot_flags(
        df,
        is_up_day=is_up_day,
        is_down_day=is_down_day,
        lookback=config.pocket_pivot_lookback_days,
        max_extension_pct=config.pocket_pivot_max_extension_pct,
    )
    df['pocket_pivot_age'] = _pocket_pivot_ages(df['pocket_pivot'])

    return df


def _normalize_trading_dates(series: pd.Series) -> pd.Series:
    """Normalize timestamps/dates to plain calendar dates for joining."""
    converted = pd.to_datetime(series, utc=True, errors="coerce")
    if converted.dt.tz is not None:
        converted = converted.dt.tz_convert("Asia/Kolkata")
    return converted.dt.normalize().dt.tz_localize(None).dt.date


def attach_rs_line_metrics(
    df: pd.DataFrame,
    index_close_by_date: pd.Series,
    *,
    lookback_days: int = 252,
) -> pd.DataFrame:
    """
    Attach RS-line metrics using an inner join on trading date.

    Stock and index calendars are aligned on overlapping dates only. Non-overlap
    rows keep NaN RS fields so local indicators remain intact.
    """
    if df.empty:
        out = df.copy()
        out["rs_line"] = np.nan
        out["rs_line_high_52w"] = np.nan
        out["rs_line_pct_off_high"] = np.nan
        return out

    if "date" not in df.columns:
        raise ValueError("attach_rs_line_metrics requires a 'date' column")

    out = df.copy()
    stock_dates = _normalize_trading_dates(out["date"])
    index_series = index_close_by_date.copy()
    if not isinstance(index_series.index, pd.DatetimeIndex):
        index_series.index = pd.to_datetime(index_series.index)
    if getattr(index_series.index, "tz", None) is not None:
        index_series.index = index_series.index.tz_convert("Asia/Kolkata").tz_localize(None)
    index_series.index = pd.Index(
        [d.date() if hasattr(d, "date") else d for d in index_series.index.normalize()]
    )
    index_series = index_series[~index_series.index.duplicated(keep="last")]

    aligned = pd.DataFrame(
        {
            "row_idx": np.arange(len(out)),
            "trade_date": stock_dates,
            "close": out["close"].astype(float),
        }
    )
    index_frame = index_series.rename("index_close").rename_axis("trade_date").reset_index()
    merged = aligned.merge(index_frame, on="trade_date", how="inner")
    merged = merged.sort_values("row_idx")
    merged["rs_line"] = merged["close"] / merged["index_close"].replace(0, np.nan)
    # Rolling high over overlapping history only (since-listing if shorter).
    merged["rs_line_high_52w"] = merged["rs_line"].rolling(
        window=lookback_days,
        min_periods=1,
    ).max()
    merged["rs_line_pct_off_high"] = (
        (merged["rs_line_high_52w"] - merged["rs_line"])
        / merged["rs_line_high_52w"].replace(0, np.nan)
        * 100.0
    )

    out["rs_line"] = np.nan
    out["rs_line_high_52w"] = np.nan
    out["rs_line_pct_off_high"] = np.nan
    out.loc[merged["row_idx"].to_numpy(), "rs_line"] = merged["rs_line"].to_numpy()
    out.loc[merged["row_idx"].to_numpy(), "rs_line_high_52w"] = (
        merged["rs_line_high_52w"].to_numpy()
    )
    out.loc[merged["row_idx"].to_numpy(), "rs_line_pct_off_high"] = (
        merged["rs_line_pct_off_high"].to_numpy()
    )
    return out


def build_equal_weight_index_closes(
    stock_frames: list[pd.DataFrame],
) -> pd.Series:
    """
    Build an equal-weight synthetic index from stock closes.

    Each stock contributes close / first_close on overlapping dates; the daily
    level is the mean of available constituents that day.
    """
    normalized_frames: list[pd.DataFrame] = []
    for frame in stock_frames:
        if frame.empty or "date" not in frame.columns or "close" not in frame.columns:
            continue
        piece = frame[["date", "close"]].copy()
        piece["trade_date"] = _normalize_trading_dates(piece["date"])
        piece = piece.dropna(subset=["trade_date", "close"])
        if piece.empty:
            continue
        first_close = float(piece.iloc[0]["close"])
        if first_close == 0 or not np.isfinite(first_close):
            continue
        piece["rel_close"] = piece["close"].astype(float) / first_close
        normalized_frames.append(piece[["trade_date", "rel_close"]])

    if not normalized_frames:
        return pd.Series(dtype=float)

    stacked = pd.concat(normalized_frames, ignore_index=True)
    level = stacked.groupby("trade_date", sort=True)["rel_close"].mean()
    level.index = pd.to_datetime(level.index)
    return level


def _compute_pocket_pivot_flags(
    df: pd.DataFrame,
    *,
    is_up_day: pd.Series,
    is_down_day: pd.Series,
    lookback: int,
    max_extension_pct: float,
) -> pd.Series:
    """True when an up-day qualifies as a pocket pivot (incl. empty down-window)."""
    closes = df["close"].to_numpy(dtype=float)
    volumes = df["volume"].to_numpy(dtype=float)
    sma_50 = df["sma_50"].to_numpy(dtype=float)
    up_flags = is_up_day.fillna(False).to_numpy(dtype=bool)
    down_flags = is_down_day.fillna(False).to_numpy(dtype=bool)
    flags = np.zeros(len(df), dtype=bool)

    for index in range(len(df)):
        if not up_flags[index]:
            continue
        if not np.isfinite(sma_50[index]) or sma_50[index] <= 0:
            continue
        extension_pct = ((closes[index] / sma_50[index]) - 1.0) * 100.0
        if extension_pct > max_extension_pct:
            continue

        start = max(0, index - lookback)
        window_down = down_flags[start:index]
        window_vol = volumes[start:index]
        if index == 0 or start == index:
            # No prior window — treat empty down-day set as satisfied.
            volume_ok = True
        else:
            down_vols = window_vol[window_down]
            if down_vols.size == 0:
                volume_ok = True
            else:
                volume_ok = volumes[index] > float(np.max(down_vols))
        flags[index] = bool(volume_ok)

    return pd.Series(flags, index=df.index, dtype=bool)


def _pocket_pivot_ages(pocket_flags: pd.Series) -> pd.Series:
    ages = np.full(len(pocket_flags), np.nan, dtype=float)
    last_hit: int | None = None
    for index, flagged in enumerate(pocket_flags.fillna(False).to_numpy(dtype=bool)):
        if flagged:
            last_hit = index
            ages[index] = 0.0
        elif last_hit is not None:
            ages[index] = float(index - last_hit)
    return pd.Series(ages, index=pocket_flags.index, dtype=float)

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


def evaluate_vcp_shortlist_criteria(
    df: pd.DataFrame,
    config: TechnicalScreeningConfig | None = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Evaluate post-Stage-2 gates used to produce the manual VCP-review shortlist."""
    config = config or TechnicalScreeningConfig()
    latest = df.iloc[-1]

    metric_names = (
        "adtv_crore",
        "pct_from_52w_high",
        "atr_ratio",
        "atr_ratio_3m_low",
        "bb_width",
        "bb_width_20th_pct",
        "bb_width_percentile",
        "avg_volume_10",
        "avg_volume_50",
        "volume_dry_up_ratio",
    )
    values = {name: float(latest[name]) for name in metric_names}
    if any(pd.isna(value) or not np.isfinite(value) for value in values.values()):
        return False, {
            "error": "Insufficient or invalid data for VCP shortlist indicators.",
            **values,
        }

    liquidity_passed = values["adtv_crore"] > config.min_adtv_crore
    pivot_distance_passed = (
        values["pct_from_52w_high"] * 100
        <= config.max_distance_52w_high_pct
    )
    atr_contraction_passed = values["atr_ratio"] <= (
        values["atr_ratio_3m_low"] * config.atr_proximity_zero
    )
    bb_contraction_passed = (
        values["bb_width_percentile"] <= config.bb_percentile_zero
    )
    squeeze_combo_passed = atr_contraction_passed and bb_contraction_passed
    volume_dry_up_passed = (
        values["volume_dry_up_ratio"] <= config.volume_ratio_zero
    )

    criteria_matches = {
        "liquidity_adtv_above_threshold": bool(liquidity_passed),
        "pivot_distance_within_threshold": bool(pivot_distance_passed),
        "atr_ratio_near_trailing_low": bool(atr_contraction_passed),
        "bb_width_below_percentile_threshold": bool(bb_contraction_passed),
        "squeeze_combo": bool(squeeze_combo_passed),
        "volume_dry_up": bool(volume_dry_up_passed),
    }
    passed = (
        liquidity_passed
        and pivot_distance_passed
        and squeeze_combo_passed
        and volume_dry_up_passed
    )

    return passed, {**values, "criteria_matches": criteria_matches}
