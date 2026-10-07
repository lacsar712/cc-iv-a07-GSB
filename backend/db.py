import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision,
    isc_a double precision,
    fill_factor double precision NOT NULL,
    est_power_w double precision,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS est_power_w double precision;
ALTER TABLE iv_scans ALTER COLUMN voc_v DROP NOT NULL;
ALTER TABLE iv_scans ALTER COLUMN isc_a DROP NOT NULL;
CREATE TABLE IF NOT EXISTS nameplate_ratings (
    id serial PRIMARY KEY,
    string_code text NOT NULL UNIQUE,
    rating_w double precision NOT NULL,
    created_by text NOT NULL,
    updated_by text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz
);
CREATE TABLE IF NOT EXISTS check_traces (
    id serial PRIMARY KEY,
    scan_id integer NOT NULL REFERENCES iv_scans(id),
    string_code text NOT NULL,
    voc_v double precision,
    isc_a double precision,
    est_power_w double precision,
    rating_w double precision,
    ff_submitted double precision,
    ff_derived double precision,
    consistent boolean NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
