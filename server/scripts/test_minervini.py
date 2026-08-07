import sys
import os
import pandas as pd
import numpy as np

# Add the server directory to path so we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.indicators import (
    compute_technical_indicators,
    compute_weighted_performance_score,
    compute_relative_strength_ratings,
    evaluate_minervini_criteria
)

def run_tests():
    print("==================================================")
    print("RUNNING MINERVINI TREND TEMPLATE AND RS TESTS")
    print("==================================================")
    
    # 1. Test Weighted Performance Score & RS Ranking
    print("\n--- 1. Testing RS Percentile Ranking Mechanism ---")
    
    # Create 5 synthetic stocks with performance scores
    stocks = [
        {"instrument_id": "stock-1", "perf_score": 10.5},
        {"instrument_id": "stock-2", "perf_score": 25.3},
        {"instrument_id": "stock-3", "perf_score": 5.2},
        {"instrument_id": "stock-4", "perf_score": 50.1},
        {"instrument_id": "stock-5", "perf_score": 18.0},
    ]
    
    rs_ratings = compute_relative_strength_ratings(stocks)
    print(f"RS ratings calculated: {rs_ratings}")
    
    # Assertions
    # Lowest score (5.2) -> rank 1 -> round(1/5 * 98) + 1 = round(19.6) + 1 = 21
    # Next (10.5) -> rank 2 -> round(2/5 * 98) + 1 = round(39.2) + 1 = 40
    # Next (18.0) -> rank 3 -> round(3/5 * 98) + 1 = round(58.8) + 1 = 60
    # Next (25.3) -> rank 4 -> round(4/5 * 98) + 1 = round(78.4) + 1 = 79
    # Highest (50.1) -> rank 5 -> round(5/5 * 98) + 1 = round(98.0) + 1 = 99
    assert rs_ratings["stock-3"] == 21, f"Expected 21, got {rs_ratings['stock-3']}"
    assert rs_ratings["stock-1"] == 40, f"Expected 40, got {rs_ratings['stock-1']}"
    assert rs_ratings["stock-5"] == 60, f"Expected 60, got {rs_ratings['stock-5']}"
    assert rs_ratings["stock-2"] == 79, f"Expected 79, got {rs_ratings['stock-2']}"
    assert rs_ratings["stock-4"] == 99, f"Expected 99, got {rs_ratings['stock-4']}"
    print("RS percentile ranking calculation is correct!")

    # 2. Test Indicators Computation
    print("\n--- 2. Testing Indicator Computation (SMA / Shift / 52w) ---")
    
    # Create a DataFrame of 350 trading days
    np.random.seed(42)
    days = 350
    # Generate prices with general upward trend
    closes = 100.0 + np.cumsum(np.random.normal(0.5, 1.0, days))
    volumes = np.random.randint(10000, 50000, days)
    
    df = pd.DataFrame({
        'high': closes + 1.0,
        'low': closes - 1.0,
        'close': closes,
        'volume': volumes
    })
    
    df_ind = compute_technical_indicators(df)
    
    # Check that columns are present
    expected_cols = [
        'sma_50', 'sma_150', 'sma_200', 'sma_200_prev', 
        'sma_200_prev_22', 'sma_200_prev_110', 
        'high_52w', 'low_52w', 'avg_volume_20', 'pct_from_52w_high',
        'adtv_crore', 'atr_ratio', 'atr_ratio_3m_low', 'bb_width',
        'bb_width_20th_pct', 'bb_width_percentile', 'volume_dry_up_ratio'
    ]
    for col in expected_cols:
        assert col in df_ind.columns, f"Expected column {col} not found in df"
        
    print("All indicator columns calculated successfully.")
    print(f"Latest 200 SMA: {df_ind.iloc[-1]['sma_200']:.2f}")
    print(f"200 SMA 1M ago: {df_ind.iloc[-1]['sma_200_prev_22']:.2f}")
    print(f"200 SMA 5M ago: {df_ind.iloc[-1]['sma_200_prev_110']:.2f}")

    # 3. Test Weighted Performance Score
    print("\n--- 3. Testing Weighted Performance Score ---")
    perf_score = compute_weighted_performance_score(df_ind)
    print(f"Calculated weighted performance score: {perf_score:.4f}")
    assert not pd.isna(perf_score), "Weighted score should not be NaN for long series"

    # 4. Test Minervini Template Evaluation & Audit Trail
    print("\n--- 4. Testing Minervini Template Checklist and Audit Trail ---")
    
    # Let's mock a stock that passes all criteria
    # We will overwrite the latest row of df_ind to satisfy all conditions
    latest_idx = len(df_ind) - 1
    
    # Set values to pass all conditions
    df_ind.at[latest_idx, 'close'] = 200.0
    df_ind.at[latest_idx, 'sma_50'] = 180.0
    df_ind.at[latest_idx, 'sma_150'] = 160.0
    df_ind.at[latest_idx, 'sma_200'] = 150.0
    df_ind.at[latest_idx, 'sma_200_prev_22'] = 145.0  # Trending up
    df_ind.at[latest_idx, 'sma_200_prev_110'] = 130.0 # Trending up 5M
    df_ind.at[latest_idx, 'high_52w'] = 220.0        # Close 200 is within 25% of high (220 * 0.75 = 165)
    df_ind.at[latest_idx, 'low_52w'] = 140.0         # Close 200 is >= 30% above low (140 * 1.3 = 182)
    df_ind.at[latest_idx, 'avg_volume_20'] = 50000
    df_ind.at[latest_idx, 'pct_from_52w_high'] = (220.0 - 200.0) / 220.0
    
    passed, metrics = evaluate_minervini_criteria(df_ind, rs_rating=85)
    print(f"Minervini passed check: {passed}")
    print(f"Metrics audit trail: {metrics['criteria_matches']}")
    
    # All booleans in audit trail should be True
    for key, val in metrics['criteria_matches'].items():
        assert val is True, f"Condition '{key}' failed (got False), expected True"
        
    assert passed is True, "Expected stock to pass Minervini checklist"
    print("Minervini evaluation and detailed audit trail verified successfully!")
    
    # Let's test a failure: Low RS rating
    passed_fail_rs, metrics_fail_rs = evaluate_minervini_criteria(df_ind, rs_rating=65)
    assert passed_fail_rs is False, "Expected failure due to low RS rating"
    assert metrics_fail_rs['criteria_matches']['rs_rating_above_threshold'] is False, "Expected RS rating condition to be False"
    print("RS rating threshold failure verified.")
    
    # Let's test a failure: 52w low rule (less than 30% above low)
    df_ind.at[latest_idx, 'low_52w'] = 160.0 # 200 / 160 = 1.25 (which is 25%, failing the new 30% rule)
    passed_fail_low, metrics_fail_low = evaluate_minervini_criteria(df_ind, rs_rating=85)
    assert passed_fail_low is False, "Expected failure due to < 30% above 52w low"
    assert metrics_fail_low['criteria_matches']['above_30pct_52w_low'] is False, "Expected above_30pct_52w_low condition to be False"
    print("52-week low 30% margin failure verified.")
    
    print("\n==================================================")
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
