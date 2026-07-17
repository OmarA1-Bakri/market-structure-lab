BEGIN;

CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS market_data.candle_reconciliation_runs (
    run_id text PRIMARY KEY CHECK (run_id ~ '^RR-[0-9]{6}$'),
    manifest_sha256 character(64) NOT NULL UNIQUE,
    manifest_json jsonb NOT NULL,
    dump_sha256 character(64) NOT NULL,
    source_row_count bigint NOT NULL CHECK (source_row_count >= 0),
    mapping_version text NOT NULL,
    cutoff timestamp with time zone NOT NULL,
    registered_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_data.candle_reconciliation_work_units (
    run_id text NOT NULL REFERENCES market_data.candle_reconciliation_runs(run_id),
    work_unit_id character(24) NOT NULL,
    manifest_sha256 character(64) NOT NULL,
    manifest_json jsonb NOT NULL,
    publication_path text NOT NULL,
    status text NOT NULL CHECK (status IN ('completed', 'source_unavailable', 'failed')),
    row_count bigint NOT NULL CHECK (row_count >= 0),
    replacement_row_count bigint NOT NULL CHECK (replacement_row_count >= 0),
    replacement_logical_sha256 character(64) NOT NULL,
    published_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, work_unit_id)
);

CREATE TABLE IF NOT EXISTS market_data.candle_reconciliation_replacements (
    replacement_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL,
    work_unit_id character(24) NOT NULL,
    symbol text NOT NULL,
    "interval" text NOT NULL CHECK ("interval" = '1m'),
    open_time bigint NOT NULL CHECK (open_time % 60000 = 0),
    classification text NOT NULL CHECK (
        classification IN ('binance_correction', 'binance_fill')
    ),
    open numeric(38, 18) NOT NULL,
    high numeric(38, 18) NOT NULL,
    low numeric(38, 18) NOT NULL,
    close numeric(38, 18) NOT NULL,
    volume numeric(38, 18) NOT NULL CHECK (volume >= 0),
    quote_volume numeric(38, 18) CHECK (quote_volume >= 0),
    trades integer CHECK (trades >= 0),
    source_name text NOT NULL,
    source_revision text NOT NULL,
    payload_sha256 character(64) NOT NULL,
    binance_row_sha256 character(64) NOT NULL,
    retrieved_at timestamp with time zone NOT NULL,
    inserted_at timestamp with time zone NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, work_unit_id)
        REFERENCES market_data.candle_reconciliation_work_units(run_id, work_unit_id),
    CONSTRAINT candle_reconciliation_replacements_ohlc_check CHECK (
        high >= open AND high >= low AND high >= close
        AND low <= open AND low <= high AND low <= close
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS candle_reconciliation_replacement_key_unique
    ON market_data.candle_reconciliation_replacements(
        run_id, symbol, "interval", open_time
    );

CREATE INDEX IF NOT EXISTS candle_reconciliation_replacement_lookup_idx
    ON market_data.candle_reconciliation_replacements(
        run_id, symbol, "interval", open_time
    );

CREATE TABLE IF NOT EXISTS market_data.candle_reconciliation_coverage (
    coverage_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL REFERENCES market_data.candle_reconciliation_runs(run_id),
    symbol text NOT NULL,
    "interval" text NOT NULL CHECK ("interval" = '1m'),
    start_time bigint NOT NULL CHECK (start_time % 60000 = 0),
    end_time bigint NOT NULL CHECK (end_time % 60000 = 0 AND end_time > start_time),
    inserted_at timestamp with time zone NOT NULL DEFAULT now(),
    UNIQUE (run_id, symbol, "interval", start_time, end_time)
);

CREATE INDEX IF NOT EXISTS candle_reconciliation_coverage_lookup_idx
    ON market_data.candle_reconciliation_coverage(
        run_id, symbol, "interval", start_time, end_time
    );

CREATE TABLE IF NOT EXISTS market_data.candle_reconciliation_promotions (
    promotion_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL UNIQUE REFERENCES market_data.candle_reconciliation_runs(run_id),
    manifest_sha256 character(64) NOT NULL,
    replacement_logical_sha256 character(64) NOT NULL,
    canonical_logical_sha256 character(64) NOT NULL,
    promoted_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION market_data.reject_candle_reconciliation_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'market_data candle reconciliation evidence is append-only';
END;
$$;

DROP TRIGGER IF EXISTS candle_reconciliation_runs_append_only
    ON market_data.candle_reconciliation_runs;
CREATE TRIGGER candle_reconciliation_runs_append_only
    BEFORE UPDATE OR DELETE ON market_data.candle_reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION market_data.reject_candle_reconciliation_mutation();

DROP TRIGGER IF EXISTS candle_reconciliation_work_units_append_only
    ON market_data.candle_reconciliation_work_units;
CREATE TRIGGER candle_reconciliation_work_units_append_only
    BEFORE UPDATE OR DELETE ON market_data.candle_reconciliation_work_units
    FOR EACH ROW EXECUTE FUNCTION market_data.reject_candle_reconciliation_mutation();

DROP TRIGGER IF EXISTS candle_reconciliation_replacements_append_only
    ON market_data.candle_reconciliation_replacements;
CREATE TRIGGER candle_reconciliation_replacements_append_only
    BEFORE UPDATE OR DELETE ON market_data.candle_reconciliation_replacements
    FOR EACH ROW EXECUTE FUNCTION market_data.reject_candle_reconciliation_mutation();

DROP TRIGGER IF EXISTS candle_reconciliation_coverage_append_only
    ON market_data.candle_reconciliation_coverage;
CREATE TRIGGER candle_reconciliation_coverage_append_only
    BEFORE UPDATE OR DELETE ON market_data.candle_reconciliation_coverage
    FOR EACH ROW EXECUTE FUNCTION market_data.reject_candle_reconciliation_mutation();

DROP TRIGGER IF EXISTS candle_reconciliation_promotions_append_only
    ON market_data.candle_reconciliation_promotions;
CREATE TRIGGER candle_reconciliation_promotions_append_only
    BEFORE UPDATE OR DELETE ON market_data.candle_reconciliation_promotions
    FOR EACH ROW EXECUTE FUNCTION market_data.reject_candle_reconciliation_mutation();

CREATE OR REPLACE VIEW market_data.candles_reconciled AS
WITH active_promotion AS (
    SELECT promotion.run_id
    FROM market_data.candle_reconciliation_promotions AS promotion
    ORDER BY promotion.promotion_id DESC
    LIMIT 1
),
active_replacements AS (
    SELECT replacement.*
    FROM market_data.candle_reconciliation_replacements AS replacement
    JOIN active_promotion AS promotion ON promotion.run_id = replacement.run_id
)
SELECT
    NULL::bigint AS source_row_id,
    replacement.symbol,
    replacement."interval",
    replacement.open_time,
    replacement.open::double precision,
    replacement.high::double precision,
    replacement.low::double precision,
    replacement.close::double precision,
    replacement.volume::double precision,
    replacement.quote_volume::double precision,
    replacement.trades,
    replacement.retrieved_at AS created_at,
    replacement.classification AS origin,
    replacement.source_name,
    replacement.payload_sha256 AS payload_checksum,
    NULL::uuid AS recovery_run_id,
    replacement.run_id AS reconciliation_run_id
FROM active_replacements AS replacement
WHERE replacement.open_time >= 1514764800000
UNION ALL
SELECT
    candle.id AS source_row_id,
    candle.symbol,
    candle."interval",
    candle.open_time,
    candle.open,
    candle.high,
    candle.low,
    candle.close,
    candle.volume,
    candle.quote_volume,
    candle.trades,
    candle.created_at,
    'dump_verified_match'::text AS origin,
    NULL::text AS source_name,
    NULL::character(64) AS payload_checksum,
    NULL::uuid AS recovery_run_id,
    coverage.run_id AS reconciliation_run_id
FROM market_data.candles AS candle
JOIN active_promotion AS promotion ON true
JOIN market_data.candle_reconciliation_coverage AS coverage
  ON coverage.run_id = promotion.run_id
 AND coverage.symbol = candle.symbol
 AND coverage."interval" = candle."interval"
 AND coverage.start_time <= candle.open_time
 AND candle.open_time < coverage.end_time
WHERE candle.open_time >= 1514764800000
  AND NOT EXISTS (
    SELECT 1
    FROM active_replacements AS replacement
    WHERE replacement.symbol = candle.symbol
      AND replacement."interval" = candle."interval"
      AND replacement.open_time = candle.open_time
);

COMMENT ON VIEW market_data.candles_reconciled IS
    'Explicitly promoted Binance-verified corrections/fills plus verified dump rows from 2018-01-01.';

COMMIT;
