"""Runs scripts/failure_recovery_drill.py: a duplicated-CCN snapshot must be blocked from
publication, the previous snapshot must stay current, and real data must be untouched.

Needs PostgreSQL running, a loaded real snapshot, and dbt/kidneylens/profiles.yml. ~15 seconds.
"""

import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_failure_recovery_drill():
    if not (ROOT / "data" / "raw" / "manifest.json").exists():
        pytest.skip("no downloaded snapshot (python -m ingestion.download)")
    if not (ROOT / "dbt" / "kidneylens" / "profiles.yml").exists():
        pytest.skip("dbt/kidneylens/profiles.yml not configured")
    try:
        psycopg.connect("host=localhost dbname=kidneylens user=kidneylens password=local_dev_password",
                        connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not running (docker compose up -d)")

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "failure_recovery_drill.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[FAIL]" not in proc.stdout
