"""Shared pytest fixtures.

Database tests use a separate `kidneylens_test` database so they never touch real data.
They are skipped (with a clear reason) if PostgreSQL is not running.
"""

import psycopg
import pytest

from ingestion.load_raw import conninfo_from_env

TEST_DB = "kidneylens_test"


@pytest.fixture(scope="session")
def test_conninfo() -> str:
    admin = conninfo_from_env()
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone()
            if not exists:
                conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not running; start it with: docker compose up -d")
    return psycopg.conninfo.make_conninfo(admin, dbname=TEST_DB)


@pytest.fixture
def db(test_conninfo):
    """A connection to an empty test database (raw schema dropped before each test)."""
    with psycopg.connect(test_conninfo, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS raw CASCADE")
        yield conn
