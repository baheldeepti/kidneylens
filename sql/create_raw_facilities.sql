-- Raw landing table for the CMS Dialysis Facility - Listing by Facility CSV.
-- One row per CSV data row per snapshot. Applied by ingestion/load_raw.py (safe to re-run).
--
-- `record` holds every source column as {"<exact CMS header>": "<exact value as text>"}.
-- JSONB is used because 10 CMS header names exceed Postgres's 63-byte column-name limit,
-- and it lets the raw layer survive CMS adding or renaming columns. Blanks stay '' (not NULL).

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.raw_facilities (
    snapshot_id        text        NOT NULL,
    source_row_number  integer     NOT NULL,
    record             jsonb       NOT NULL,
    source_file        text        NOT NULL,
    source_url         text        NOT NULL,
    downloaded_at      timestamptz NOT NULL,
    source_sha256      text        NOT NULL,
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, source_row_number)
);
