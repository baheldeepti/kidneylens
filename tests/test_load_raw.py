"""Tests for ingestion/load_raw.py.

Tests marked with the `db` fixture need PostgreSQL (docker compose up -d); the rest do not.
"""

from datetime import datetime, timezone

import psycopg
import pytest

from ingestion import download
from ingestion.download import save_snapshot
from ingestion.load_raw import LoadError, load_records, read_snapshot, select_snapshot

URL = "https://example.test/files/DFC_FACILITY.csv"
HEADER = ",".join(download.REQUIRED_COLUMNS)
CSV_A = (
    HEADER + "\n"
    "012306,CHILDRENS HOSPITAL DIALYSIS,AL,03561,,Yes,No\n"
    '"052345","DIALYSIS CENTER, NORTH",CA,90001,4.0,No,\n'
    "063333,LONE STAR KIDNEY,TX,75001,3.0,Yes,Yes\n"
).encode("utf-8")
CSV_B = CSV_A + b"072501,DaVita Bridgeport Dialysis,CT,06606,3.0,Yes,Yes\n"

T1 = datetime(2026, 10, 3, 5, 30, 20, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def raw_dir(tmp_path):
    """A data/raw folder holding two downloaded snapshots: A (3 rows) then B (4 rows)."""
    save_snapshot(CSV_A, URL, tmp_path, T1)
    save_snapshot(CSV_B, URL, tmp_path, T2)
    return tmp_path


def count_rows(conn, snapshot_id=None) -> int:
    if conn.execute("SELECT to_regclass('raw.raw_facilities')").fetchone()[0] is None:
        return 0  # table does not exist
    sql = "SELECT count(*) FROM raw.raw_facilities"
    if snapshot_id:
        return conn.execute(sql + " WHERE snapshot_id = %s", (snapshot_id,)).fetchone()[0]
    return conn.execute(sql).fetchone()[0]


# --- No database needed -------------------------------------------------------

def test_select_snapshot_defaults_to_latest(raw_dir):
    assert select_snapshot(raw_dir)["path"] == "20261101T000000Z/DFC_FACILITY.csv"


def test_select_snapshot_by_id(raw_dir):
    assert select_snapshot(raw_dir, "20261003T053020Z")["row_count"] == 3


def test_select_unknown_snapshot_fails(raw_dir):
    with pytest.raises(LoadError, match="not in manifest"):
        select_snapshot(raw_dir, "19990101T000000Z")


def test_select_with_empty_manifest_fails(tmp_path):
    with pytest.raises(LoadError, match="No snapshots"):
        select_snapshot(tmp_path)


def test_read_snapshot_keeps_values_as_exact_text(raw_dir):
    records = read_snapshot(raw_dir, select_snapshot(raw_dir, "20261003T053020Z"))
    assert len(records) == 3
    first = records[0]
    assert first["CMS Certification Number (CCN)"] == "012306"  # leading zero kept
    assert first["ZIP Code"] == "03561"
    assert first["Five Star"] == ""  # blank stays blank, not None or 0
    assert records[1]["Facility Name"] == "DIALYSIS CENTER, NORTH"
    assert records[1]["Five Star"] == "4.0"  # not converted to 4


def test_read_snapshot_refuses_modified_file(raw_dir):
    entry = select_snapshot(raw_dir, "20261003T053020Z")
    (raw_dir / entry["path"]).write_bytes(CSV_B)  # file changed after download
    with pytest.raises(LoadError, match="no longer matches the SHA-256"):
        read_snapshot(raw_dir, entry)


# --- PostgreSQL needed --------------------------------------------------------

def load(db, raw_dir, snapshot_id):
    entry = select_snapshot(raw_dir, snapshot_id)
    return load_records(db, entry, read_snapshot(raw_dir, entry))


def test_load_inserts_all_rows_with_metadata(db, raw_dir):
    assert load(db, raw_dir, "20261003T053020Z") == ("loaded", 3)

    row = db.execute(
        "SELECT snapshot_id, source_row_number, record, source_file, source_url,"
        " downloaded_at, source_sha256 FROM raw.raw_facilities WHERE source_row_number = 1"
    ).fetchone()
    assert row[0] == "20261003T053020Z"
    assert row[1] == 1
    assert row[2]["CMS Certification Number (CCN)"] == "012306"
    assert row[2]["ZIP Code"] == "03561"
    assert row[2]["Five Star"] == ""
    assert row[3] == "20261003T053020Z/DFC_FACILITY.csv"
    assert row[4] == URL
    assert row[5] == T1
    assert row[6] == download.hashlib.sha256(CSV_A).hexdigest()


def test_all_rows_share_one_loaded_at(db, raw_dir):
    load(db, raw_dir, "20261003T053020Z")
    assert db.execute("SELECT count(DISTINCT loaded_at) FROM raw.raw_facilities").fetchone()[0] == 1


def test_reloading_same_snapshot_is_a_noop(db, raw_dir):
    load(db, raw_dir, "20261003T053020Z")
    assert load(db, raw_dir, "20261003T053020Z") == ("already_loaded", 3)
    assert count_rows(db) == 3


def test_two_snapshots_are_kept_separately(db, raw_dir):
    load(db, raw_dir, "20261003T053020Z")
    load(db, raw_dir, "20261101T000000Z")
    assert count_rows(db, "20261003T053020Z") == 3
    assert count_rows(db, "20261101T000000Z") == 4


def test_failure_mid_load_rolls_back_everything(db, raw_dir):
    load(db, raw_dir, "20261003T053020Z")  # an earlier, good snapshot

    entry = select_snapshot(raw_dir, "20261101T000000Z")
    records = read_snapshot(raw_dir, entry)
    # Postgres rejects the NUL character in JSONB, so row 4 fails after rows 1-3 were inserted.
    records[3]["Facility Name"] = "BAD\x00NAME"

    with pytest.raises(psycopg.errors.UntranslatableCharacter):
        load_records(db, entry, records)

    assert count_rows(db, "20261101T000000Z") == 0  # no partial snapshot
    assert count_rows(db, "20261003T053020Z") == 3  # earlier snapshot untouched


def test_failure_on_first_ever_load_leaves_nothing(db, raw_dir):
    entry = select_snapshot(raw_dir, "20261003T053020Z")
    records = read_snapshot(raw_dir, entry)
    records[2]["State"] = "\x00"

    with pytest.raises(psycopg.errors.UntranslatableCharacter):
        load_records(db, entry, records)

    # Even the CREATE TABLE was rolled back.
    assert db.execute("SELECT to_regclass('raw.raw_facilities')").fetchone()[0] is None


def test_retry_after_failure_succeeds(db, raw_dir):
    entry = select_snapshot(raw_dir, "20261003T053020Z")
    bad = read_snapshot(raw_dir, entry)
    bad[2]["State"] = "\x00"
    with pytest.raises(psycopg.errors.UntranslatableCharacter):
        load_records(db, entry, bad)

    assert load(db, raw_dir, "20261003T053020Z") == ("loaded", 3)
