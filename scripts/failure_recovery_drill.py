"""Failure-recovery drill: a bad CMS snapshot (duplicated CCN) must not reach the published tables.

Runs the REAL pipeline commands (ingestion.download, ingestion.load_raw, dbt build) against an
ISOLATED copy: a temporary data folder and a separate database `kidneylens_drill`. The real
snapshot in data/raw/ is only read, and the real `kidneylens` database is only read
(fingerprinted before and after). Everything the drill creates is removed at the end.

Usage (from the repo root, with `docker compose up -d` running):
    .venv/bin/python scripts/failure_recovery_drill.py          # run and clean up
    .venv/bin/python scripts/failure_recovery_drill.py --keep   # keep the drill DB/folder to inspect

Exit code 0 = every check passed.
"""

import argparse
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path(sys.executable)          # the virtualenv running this script
DBT = PYTHON.parent / "dbt"
DBT_PROJECT = ROOT / "dbt" / "kidneylens"
REAL_RAW_DIR = ROOT / "data" / "raw"
PROD_DB = os.getenv("POSTGRES_DB", "kidneylens")
DRILL_DB = "kidneylens_drill"
ADMIN = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    user=os.getenv("POSTGRES_USER", "kidneylens"),
    password=os.getenv("POSTGRES_PASSWORD", "local_dev_password"),
)

results: list[tuple[bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    say(f"   [{'PASS' if ok else 'FAIL'}] {label}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(db: str, sql: str, params=None) -> list[tuple]:
    with psycopg.connect(dbname=db, autocommit=True, **ADMIN) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []  # DDL returns no rows


def run(cmd: list, env: dict | None = None, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(c) for c in cmd], cwd=cwd, env={**os.environ, **(env or {})},
        capture_output=True, text=True, timeout=600,
    )


# --- Fingerprints -------------------------------------------------------------------

def db_fingerprint(db: str) -> dict:
    """Checksums of raw, dim and mart contents (order-independent of physical storage)."""
    fp = {}
    fp["raw"] = query(db, """
        select snapshot_id, count(*), md5(string_agg(record::text, '|' order by source_row_number))
        from raw.raw_facilities group by snapshot_id order by snapshot_id""")
    fp["dim"] = query(db, """
        select min(snapshot_id), count(*), md5(string_agg(t::text, '|' order by ccn))
        from analytics.dim_facility_current t""")
    fp["mart"] = query(db, """
        select count(*), md5(string_agg(t::text, '|' order by state))
        from analytics.mart_state_services t""")
    return fp


def files_fingerprint(raw_dir: Path) -> dict:
    return {str(p.relative_to(raw_dir)): sha256_file(p) for p in sorted(raw_dir.rglob("*")) if p.is_file()}


# --- Local stand-in for the CMS website ----------------------------------------------

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/DFC_FACILITY.csv"


# --- Pipeline steps against the isolated copy ---------------------------------------

def make_dbt_profile(work: Path) -> Path:
    """A dbt profile that can only point at the drill database."""
    profiles_dir = work / "dbt_profiles"
    profiles_dir.mkdir()
    (profiles_dir / "profiles.yml").write_text(json.dumps({  # JSON is valid YAML
        "kidneylens": {"target": "drill", "outputs": {"drill": {
            "type": "postgres", "host": ADMIN["host"], "port": int(ADMIN["port"]),
            "user": ADMIN["user"], "password": ADMIN["password"],
            "dbname": DRILL_DB, "schema": "analytics", "threads": 4,
        }}}
    }))
    return profiles_dir


def dbt(command: str, work: Path, profiles_dir: Path) -> subprocess.CompletedProcess:
    return run(
        [DBT, command, "--no-use-colors", "--profiles-dir", profiles_dir,
         "--target-path", work / "dbt_target", "--log-path", work / "dbt_logs"],
        cwd=DBT_PROJECT,
    )


def download(url: str, raw_dir: Path) -> subprocess.CompletedProcess:
    return run([PYTHON, "-m", "ingestion.download", "--url", url, "--raw-dir", raw_dir])


def load(raw_dir: Path) -> subprocess.CompletedProcess:
    return run([PYTHON, "-m", "ingestion.load_raw", "--raw-dir", raw_dir], env={"POSTGRES_DB": DRILL_DB})


def latest_snapshot_id(raw_dir: Path) -> str:
    return json.loads((raw_dir / "manifest.json").read_text())["snapshots"][-1]["path"].split("/")[0]


def dbt_lines(proc: subprocess.CompletedProcess, *needles: str) -> list[str]:
    out = []
    for line in proc.stdout.splitlines():
        if any(n in line for n in needles) and " START " not in line:
            out.append("      " + line.split("  ", 1)[-1].strip()[:110])
    return out


# --- The drill ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="keep the drill database and folder")
    args = parser.parse_args()

    manifest = json.loads((REAL_RAW_DIR / "manifest.json").read_text())["snapshots"]
    real_csv = REAL_RAW_DIR / manifest[-1]["path"]

    say("== 0. Fingerprint production-like data (read-only)")
    prod_files_before = files_fingerprint(REAL_RAW_DIR)
    prod_db_before = db_fingerprint(PROD_DB)
    say(f"   real snapshot: {real_csv.relative_to(ROOT)}  sha256 {sha256_file(real_csv)[:16]}...")
    say(f"   production dim: snapshot {prod_db_before['dim'][0][0]}, {prod_db_before['dim'][0][1]} rows")

    work = Path(tempfile.mkdtemp(prefix="kidneylens_drill_"))
    serve_dir, raw_dir = work / "cms_site", work / "raw"
    serve_dir.mkdir()
    server = None
    try:
        query(PROD_DB, f"drop database if exists {DRILL_DB}")
        query(PROD_DB, f"create database {DRILL_DB}")
        query(DRILL_DB, (ROOT / "sql" / "bootstrap.sql").read_text())
        profiles_dir = make_dbt_profile(work)
        server, url = start_server(serve_dir)
        say(f"   isolated copy: folder {work}, database {DRILL_DB}, fake CMS at {url}")

        say("\n== 1. Baseline: a good snapshot (exact copy of the real one) is published")
        good = real_csv.read_bytes()
        (serve_dir / "DFC_FACILITY.csv").write_bytes(good)
        check(download(url, raw_dir).returncode == 0, "downloader saved the good snapshot")
        good_id = latest_snapshot_id(raw_dir)
        check(load(raw_dir).returncode == 0, f"loader loaded snapshot {good_id}")
        build = dbt("build", work, profiles_dir)
        check(build.returncode == 0, "dbt build passed")
        baseline = db_fingerprint(DRILL_DB)
        check(baseline["dim"][0][0] == good_id, f"dim_facility_current is snapshot {good_id} ({baseline['dim'][0][1]} rows)")

        time.sleep(1.1)  # snapshot folders are named by UTC second

        say("\n== 2. A bad snapshot arrives: one CCN appears twice")
        lines = good.splitlines(keepends=True)
        bad = good + lines[1]  # duplicate the first facility row (CCN 012306)
        (serve_dir / "DFC_FACILITY.csv").write_bytes(bad)
        dl = download(url, raw_dir)
        bad_id = latest_snapshot_id(raw_dir)
        check(dl.returncode == 0 and bad_id != good_id,
              f"ingestion RECEIVED it as new snapshot {bad_id} (well-formed CSV, new SHA-256)")
        ld = load(raw_dir)
        bad_rows = query(DRILL_DB, "select count(*) from raw.raw_facilities where snapshot_id = %s", (bad_id,))[0][0]
        dupes = query(DRILL_DB, """
            select record->>'CMS Certification Number (CCN)', count(*) from raw.raw_facilities
            where snapshot_id = %s group by 1 having count(*) > 1""", (bad_id,))
        check(ld.returncode == 0 and bad_rows == len(lines), f"loader stored it in raw as received ({bad_rows} rows)")
        check(dupes == [("012306", 2)], f"raw layer now holds duplicated CCN: {dupes}")

        say("\n== 3. Validation detects the problem and publication is blocked")
        build = dbt("build", work, profiles_dir)
        for line in dbt_lines(build, "FAIL ", "SKIP relation", "Done."):
            say(line)
        check(build.returncode != 0, "dbt build FAILED (non-zero exit)")
        check("FAIL 1 assert_stg_facilities_one_row_per_snapshot_ccn" in build.stdout,
              "grain test caught the duplicated CCN")
        check("SKIP relation analytics.dim_facility_current" in build.stdout
              and "SKIP relation analytics.mart_state_services" in build.stdout,
              "dim_facility_current and mart_state_services were NOT rebuilt")

        say("\n== 4. The previous valid snapshot remains current")
        after_build = db_fingerprint(DRILL_DB)
        check(after_build["dim"] == baseline["dim"], f"dim unchanged: still snapshot {good_id}, identical checksum")
        check(after_build["mart"] == baseline["mart"], "mart unchanged: identical checksum")
        run_only = dbt("run", work, profiles_dir)  # models without tests: second line of defense
        after_run = db_fingerprint(DRILL_DB)
        check(run_only.returncode == 0 and after_run["dim"][0][0] == good_id,
              f"even `dbt run` (no tests) keeps {good_id}: the model's own validation rejects {bad_id}")
        check(after_run["dim"] == baseline["dim"] and after_run["mart"] == baseline["mart"],
              "dim and mart contents identical after `dbt run`")

        say("\n== 5. No production-like data was touched")
        check(files_fingerprint(REAL_RAW_DIR) == prod_files_before,
              "data/raw/ unchanged (same files, same SHA-256 for the CSV and manifest)")
        check(db_fingerprint(PROD_DB) == prod_db_before,
              f"database '{PROD_DB}' unchanged (raw, dim and mart checksums identical)")

        say("\n== 6. Recovery: quarantine the bad snapshot (drill database only) and rebuild")
        query(DRILL_DB, "create schema if not exists quarantine")
        query(DRILL_DB, "create table quarantine.raw_facilities (like raw.raw_facilities including all)")
        query(DRILL_DB, "insert into quarantine.raw_facilities select * from raw.raw_facilities where snapshot_id = %s",
              (bad_id,))
        query(DRILL_DB, "delete from raw.raw_facilities where snapshot_id = %s", (bad_id,))
        build = dbt("build", work, profiles_dir)
        recovered = db_fingerprint(DRILL_DB)
        check(build.returncode == 0, "dbt build is green again")
        check(recovered["dim"] == baseline["dim"], f"dim still serves {good_id}")
        kept = query(DRILL_DB, "select count(*) from quarantine.raw_facilities")[0][0]
        check(kept == bad_rows, f"bad snapshot preserved in quarantine for review ({kept} rows), not destroyed")
    finally:
        if server:
            server.shutdown()
        if args.keep:
            say(f"\n(kept: folder {work}, database {DRILL_DB})")
        else:
            query(PROD_DB, f"drop database if exists {DRILL_DB} with (force)")
            shutil.rmtree(work, ignore_errors=True)
            say(f"\n(cleaned up: dropped database {DRILL_DB}, removed {work})")

    failed = [label for ok, label in results if not ok]
    say(f"\nRESULT: {len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
