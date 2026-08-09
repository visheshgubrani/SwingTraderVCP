-- Nifty 500 index instrument for vcp_score_v3 RS-line benchmark.
-- Confirmed in Fyers symbol master as NSE:NIFTY500-INDEX.
BEGIN;

INSERT INTO instruments (
    exchange,
    segment,
    symbol,
    trading_symbol,
    fyers_symbol,
    name,
    lot_size,
    tick_size,
    active,
    metadata
)
SELECT
    'NSE',
    'INDEX',
    'NIFTY500',
    'NIFTY500-INDEX',
    'NSE:NIFTY500-INDEX',
    'Nifty 500 Index',
    1,
    0.05,
    true,
    '{"role": "rs_benchmark", "pipeline": "vcp_score_v3"}'::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM instruments WHERE fyers_symbol = 'NSE:NIFTY500-INDEX'
);

COMMIT;
