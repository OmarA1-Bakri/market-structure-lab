CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS market_data.candle_recovery_runs (
    run_id uuid PRIMARY KEY,
    manifest_sha256 character(64) NOT NULL UNIQUE,
    dump_sha256 character(64) NOT NULL,
    source_row_count bigint NOT NULL CHECK (source_row_count >= 0),
    mapping_version text NOT NULL,
    recovery_as_of timestamp with time zone NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    cursor_gap_id text,
    logical_hash character(64),
    error_detail text,
    started_at timestamp with time zone NOT NULL DEFAULT now(),
    completed_at timestamp with time zone
);

CREATE TABLE IF NOT EXISTS market_data.candle_supplements (
    supplement_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES market_data.candle_recovery_runs(run_id),
    source_name text NOT NULL,
    source_revision text NOT NULL,
    symbol text NOT NULL,
    "interval" text NOT NULL CHECK ("interval" = '1m'),
    open_time bigint NOT NULL CHECK (open_time % 60000 = 0),
    open numeric(38, 18) NOT NULL,
    high numeric(38, 18) NOT NULL,
    low numeric(38, 18) NOT NULL,
    close numeric(38, 18) NOT NULL,
    volume numeric(38, 18) NOT NULL CHECK (volume >= 0),
    quote_volume numeric(38, 18) CHECK (quote_volume >= 0),
    trades integer CHECK (trades >= 0),
    retrieved_at timestamp with time zone NOT NULL,
    payload_checksum character(64) NOT NULL,
    row_checksum character(64) NOT NULL,
    validation_status text NOT NULL CHECK (
        validation_status IN ('validated', 'source_conflict', 'invalid')
    ),
    inserted_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT candle_supplements_ohlc_check CHECK (
        high >= open AND high >= low AND high >= close
        AND low <= open AND low <= high AND low <= close
    ),
    CONSTRAINT candle_supplements_source_row_unique UNIQUE (
        source_name, symbol, "interval", open_time, row_checksum
    )
);

CREATE INDEX IF NOT EXISTS candle_supplements_lookup_idx
    ON market_data.candle_supplements(symbol, "interval", open_time)
    WHERE validation_status = 'validated';

CREATE UNIQUE INDEX IF NOT EXISTS candle_supplements_validated_key_unique
    ON market_data.candle_supplements(symbol, "interval", open_time)
    WHERE validation_status = 'validated';

CREATE TABLE IF NOT EXISTS market_data.candle_recovery_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES market_data.candle_recovery_runs(run_id),
    gap_id text NOT NULL,
    batch_start bigint NOT NULL,
    batch_end bigint NOT NULL CHECK (batch_end > batch_start),
    payload_checksum character(64),
    row_count integer NOT NULL CHECK (row_count >= 0 AND row_count <= 1000),
    status text NOT NULL CHECK (status IN ('completed', 'failed')),
    failure_detail text,
    checkpointed_at timestamp with time zone NOT NULL DEFAULT now(),
    UNIQUE (run_id, gap_id, batch_start, batch_end, payload_checksum)
);

CREATE TABLE IF NOT EXISTS market_data.candle_gap_resolutions (
    run_id uuid NOT NULL REFERENCES market_data.candle_recovery_runs(run_id),
    gap_id text NOT NULL,
    symbol text NOT NULL,
    "interval" text NOT NULL CHECK ("interval" = '1m'),
    gap_start bigint NOT NULL,
    gap_end bigint NOT NULL CHECK (gap_end > gap_start),
    resolution text NOT NULL CHECK (resolution IN (
        'recovered', 'partially_recovered', 'provider_absent', 'non_trading',
        'source_unavailable', 'source_conflict', 'fetch_failed', 'unresolved'
    )),
    expected_minutes bigint NOT NULL CHECK (expected_minutes > 0),
    recovered_minutes bigint NOT NULL CHECK (
        recovered_minutes >= 0 AND recovered_minutes <= expected_minutes
    ),
    reason text NOT NULL,
    source_name text NOT NULL,
    classified_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, gap_id)
);

-- Backfill columns when this same version is reapplied after an interrupted development run.
ALTER TABLE market_data.candle_gap_resolutions ADD COLUMN IF NOT EXISTS symbol text;
ALTER TABLE market_data.candle_gap_resolutions ADD COLUMN IF NOT EXISTS "interval" text;
ALTER TABLE market_data.candle_gap_resolutions ADD COLUMN IF NOT EXISTS gap_start bigint;
ALTER TABLE market_data.candle_gap_resolutions ADD COLUMN IF NOT EXISTS gap_end bigint;

CREATE OR REPLACE FUNCTION market_data.reject_candle_supplement_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'market_data.candle_supplements is append-only';
END;
$$;

DROP TRIGGER IF EXISTS candle_supplements_append_only
    ON market_data.candle_supplements;
CREATE TRIGGER candle_supplements_append_only
    BEFORE UPDATE OR DELETE ON market_data.candle_supplements
    FOR EACH ROW EXECUTE FUNCTION market_data.reject_candle_supplement_mutation();

CREATE OR REPLACE VIEW market_data.candles_canonical AS
WITH ranked_supplements AS (
    SELECT supplement.*,
           row_number() OVER (
               PARTITION BY symbol, "interval", open_time
               ORDER BY retrieved_at, row_checksum, supplement_id
           ) AS source_rank
    FROM market_data.candle_supplements AS supplement
    WHERE validation_status = 'validated'
)
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
    'dump'::text AS origin,
    NULL::text AS source_name,
    NULL::character(64) AS payload_checksum,
    NULL::uuid AS recovery_run_id
FROM market_data.candles AS candle
WHERE candle.open_time >= 1514764800000
UNION ALL
SELECT
    NULL::bigint AS source_row_id,
    supplement.symbol,
    supplement."interval",
    supplement.open_time,
    supplement.open::double precision,
    supplement.high::double precision,
    supplement.low::double precision,
    supplement.close::double precision,
    supplement.volume::double precision,
    supplement.quote_volume::double precision,
    supplement.trades,
    supplement.retrieved_at AS created_at,
    'supplement'::text AS origin,
    supplement.source_name,
    supplement.payload_checksum,
    supplement.run_id AS recovery_run_id
FROM ranked_supplements AS supplement
WHERE supplement.source_rank = 1
  AND supplement.open_time >= 1514764800000
  AND NOT EXISTS (
      SELECT 1
      FROM market_data.candles AS candle
      WHERE candle.symbol = supplement.symbol
        AND candle."interval" = supplement."interval"
        AND candle.open_time = supplement.open_time
  );

COMMENT ON TABLE market_data.candle_supplements IS
    'Append-only real source observations; never synthesized or used to modify restored candles.';
COMMENT ON VIEW market_data.candles_canonical IS
    'Deterministic dump-preferred union from 2018-01-01 of restored and validated supplement candles.';
