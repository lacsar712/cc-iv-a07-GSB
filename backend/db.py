import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


# 铭牌功率对照表种子：当量（估算功率取整 W）→ 标准填充因子
# 375W = 41.2V × 9.1A 取整 → 0.78 合格；319W = 38.0V × 8.4A 取整 → 0.61 衰减
LOOKUP_SEED = [
    (375, 0.78, "阵列A-串03 铭牌当量"),
    (319, 0.61, "阵列B-串11 铭牌当量"),
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision,
    isc_a double precision,
    fill_factor double precision,
    est_power_w double precision,
    power_equiv_w integer,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
-- 兼容旧卷：补出新增列，并放宽早期 NOT NULL 约束
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS est_power_w double precision;
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS power_equiv_w integer;
ALTER TABLE iv_scans ALTER COLUMN voc_v DROP NOT NULL;
ALTER TABLE iv_scans ALTER COLUMN isc_a DROP NOT NULL;
ALTER TABLE iv_scans ALTER COLUMN fill_factor DROP NOT NULL;

-- 铭牌当量 → 标准填充因子 对照表
CREATE TABLE IF NOT EXISTS ff_lookup (
    power_equiv_w integer PRIMARY KEY,
    std_fill_factor double precision NOT NULL,
    note text,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL
);

-- 对表痕迹：每次从铭牌页提交都留痕，与扫描进队同一次记账
CREATE TABLE IF NOT EXISTS lookup_traces (
    id serial PRIMARY KEY,
    scan_id integer REFERENCES iv_scans(id) ON DELETE CASCADE,
    string_code text NOT NULL,
    voc_v double precision,
    isc_a double precision,
    est_power_w double precision,
    power_equiv_w integer NOT NULL,
    ff_from_table double precision NOT NULL,
    ff_direct double precision,
    ff_used double precision NOT NULL,
    input_mode text NOT NULL,
    match text NOT NULL,
    verdict text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lookup_traces_equiv ON lookup_traces(power_equiv_w);
CREATE INDEX IF NOT EXISTS idx_lookup_traces_scan ON lookup_traces(scan_id);

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
