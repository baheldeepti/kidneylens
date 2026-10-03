"""Load a downloaded CMS snapshot into raw.raw_facilities in PostgreSQL.

All rows of a snapshot are inserted in ONE transaction: either every row is saved or none are.
Loading a snapshot that is already in the table is a no-op.

Usage:
    python -m ingestion.load_raw                      # latest snapshot in data/raw/manifest.json
    python -m ingestion.load_raw --snapshot-id 20261003T053020Z
"""

import argparse
import csv
import hashlib
import io
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from ingestion.download import DEFAULT_RAW_DIR, MalformedCSVError, load_manifest, validate_csv

DDL_PATH = Path(__file__).resolve().parent.parent / "sql" / "create_raw_facilities.sql"

INSERT_SQL = """
    INSERT INTO raw.raw_facilities
        (snapshot_id, source_row_number, record, source_file, source_url, downloaded_at, source_sha256)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


class LoadError(Exception):
    """The snapshot cannot be loaded (missing, changed on disk, or not in the manifest)."""


def conninfo_from_env() -> str:
    """Connection settings from environment / .env, defaulting to docker-compose.yml values."""
    return psycopg.conninfo.make_conninfo(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "kidneylens"),
        user=os.getenv("POSTGRES_USER", "kidneylens"),
        password=os.getenv("POSTGRES_PASSWORD", "local_dev_password"),
    )


def snapshot_id_of(entry: dict) -> str:
    """The snapshot folder name, e.g. '20261003T053020Z/DFC_FACILITY.csv' -> '20261003T053020Z'."""
    return Path(entry["path"]).parts[0]


def select_snapshot(raw_dir: Path, snapshot_id: str | None = None) -> dict:
    """Return the manifest entry for snapshot_id, or the most recent download if None."""
    snapshots = load_manifest(raw_dir)
    if not snapshots:
        raise LoadError(f"No snapshots in {raw_dir}/manifest.json. Run: python -m ingestion.download")
    if snapshot_id is None:
        return snapshots[-1]
    for entry in snapshots:
        if snapshot_id_of(entry) == snapshot_id:
            return entry
    known = [snapshot_id_of(e) for e in snapshots]
    raise LoadError(f"Snapshot {snapshot_id!r} not in manifest. Known snapshots: {known}")


def read_snapshot(raw_dir: Path, entry: dict) -> list[dict]:
    """Read the snapshot file as a list of {header: value} dicts, with every value kept as text."""
    path = raw_dir / entry["path"]
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise LoadError(f"Snapshot file is missing: {path}") from exc

    if hashlib.sha256(content).hexdigest() != entry["sha256"]:
        raise LoadError(f"{path} no longer matches the SHA-256 recorded at download. Refusing to load.")

    validate_csv(content)  # same structural checks as at download time
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    return list(reader)


def load_records(conn: psycopg.Connection, entry: dict, records: list[dict]) -> tuple[str, int]:
    """Insert records for one snapshot in a single transaction.

    Returns ("loaded", rows inserted) or ("already_loaded", rows already present).
    If anything fails, the transaction is rolled back and no rows from this snapshot remain.
    """
    snapshot_id = snapshot_id_of(entry)
    with conn.transaction():
        conn.execute(DDL_PATH.read_text())
        (existing,) = conn.execute(
            "SELECT count(*) FROM raw.raw_facilities WHERE snapshot_id = %s", (snapshot_id,)
        ).fetchone()
        if existing:
            return "already_loaded", existing

        rows = [
            (
                snapshot_id,
                row_number,
                Jsonb(record),
                entry["path"],
                entry["source_url"],
                entry["downloaded_at_utc"],
                entry["sha256"],
            )
            for row_number, record in enumerate(records, start=1)
        ]
        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, rows)
    return "loaded", len(rows)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Load a CMS snapshot into raw.raw_facilities.")
    parser.add_argument("--snapshot-id", help="snapshot folder name; default: latest download")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args(argv)

    try:
        entry = select_snapshot(args.raw_dir, args.snapshot_id)
        records = read_snapshot(args.raw_dir, entry)
    except (LoadError, MalformedCSVError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    snapshot_id = snapshot_id_of(entry)
    try:
        # autocommit=True: nothing is implicit; the only transaction is the explicit one in load_records.
        with psycopg.connect(conninfo_from_env(), autocommit=True) as conn:
            status, count = load_records(conn, entry, records)
    except psycopg.OperationalError as exc:
        print(
            "ERROR: database connection problem (is PostgreSQL running? docker compose up -d).\n"
            f"Any partial load was rolled back.\n{exc}",
            file=sys.stderr,
        )
        return 1
    except psycopg.Error as exc:
        print(f"ERROR: load failed and was rolled back; no rows were saved.\n{exc}", file=sys.stderr)
        return 1

    if status == "already_loaded":
        print(f"No change: snapshot {snapshot_id} is already loaded ({count} rows).")
    else:
        print(f"Loaded {count} rows for snapshot {snapshot_id} into raw.raw_facilities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
