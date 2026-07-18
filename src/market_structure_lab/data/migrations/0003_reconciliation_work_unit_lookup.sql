CREATE INDEX IF NOT EXISTS candle_reconciliation_replacements_work_unit_lookup_idx
    ON market_data.candle_reconciliation_replacements(run_id, work_unit_id);
