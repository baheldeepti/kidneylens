"""Prove the Streamlit role `kidneylens_app` can only read the two published tables.

Each forbidden statement runs with the read-only session setting switched OFF, so the test
shows that missing PRIVILEGES block it, not just the (bypassable) read-only default.

Needs: docker compose up -d, sql/create_app_role.sql applied, dbt build.
"""

import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

import queries

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = ["analytics.dim_facility_current", "analytics.mart_state_services"]


def connect(read_only_default: bool = True) -> psycopg.Connection:
    """Connect as kidneylens_app. read_only_default=False overrides the role's read-only setting."""
    options = "-c default_transaction_read_only=" + ("on" if read_only_default else "off")
    conninfo = psycopg.conninfo.make_conninfo(queries.conninfo_from_env(), options=options)
    return psycopg.connect(conninfo, autocommit=True)


@pytest.fixture(scope="module", autouse=True)
def require_app_role():
    try:
        with connect() as conn:
            conn.execute("select 1")
    except psycopg.OperationalError as exc:
        pytest.skip(f"kidneylens_app cannot connect (run sql/create_app_role.sql): {exc}")


# --- What it CAN do ---------------------------------------------------------------

@pytest.mark.parametrize("table", PUBLISHED)
def test_can_select_published_tables(table):
    with connect() as conn:
        assert conn.execute(f"select count(*) from {table}").fetchone()[0] > 0


def test_holds_exactly_two_select_privileges_and_no_role_attributes():
    with connect() as conn:
        grants = conn.execute(
            "select table_schema || '.' || table_name, privilege_type"
            " from information_schema.role_table_grants where grantee = 'kidneylens_app'"
            " order by 1"
        ).fetchall()
        attrs = conn.execute(
            "select rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls"
            " from pg_roles where rolname = 'kidneylens_app'"
        ).fetchone()
    assert grants == [(t, "SELECT") for t in PUBLISHED]
    assert attrs == (False, False, False, False, False)


# --- What it CANNOT do (privileges block it even with read-only switched off) ---------

FORBIDDEN = {
    "insert": "insert into analytics.mart_state_services (state) values ('ZZ')",
    "update": "update analytics.mart_state_services set facility_count = 0",
    "delete": "delete from analytics.dim_facility_current",
    "truncate": "truncate analytics.mart_state_services",
    "drop_table": "drop table analytics.mart_state_services",
    "alter_table": "alter table analytics.dim_facility_current add column x int",
    "alter_schema": "alter schema analytics rename to x",
    "drop_schema": "drop schema analytics cascade",
    "create_table_analytics": "create table analytics.x (i int)",
    "create_view_analytics": "create view analytics.v as select 1",
    "create_table_public": "create table public.x (i int)",
    "create_temp_table": "create temp table x (i int)",
    "create_schema": "create schema x",
    "create_database": "create database x",
    "create_role": "create role x",
    "read_raw": "select count(*) from raw.raw_facilities",
    "read_staging": "select count(*) from analytics.stg_facilities",
}


@pytest.mark.parametrize("sql", FORBIDDEN.values(), ids=FORBIDDEN.keys())
def test_forbidden_even_without_read_only_mode(sql):
    with connect(read_only_default=False) as conn:
        assert conn.execute("show default_transaction_read_only").fetchone()[0] == "off"
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql)


def test_read_only_session_is_a_second_layer():
    with connect() as conn:  # role default: read-only
        assert conn.execute("show default_transaction_read_only").fetchone()[0] == "on"
        with pytest.raises(errors.ReadOnlySqlTransaction):
            conn.execute("delete from analytics.mart_state_services")


def test_streamlit_connection_uses_the_app_role():
    with psycopg.connect(queries.conninfo_from_env()) as conn:
        user, read_only = conn.execute("select current_user, current_setting('transaction_read_only')").fetchone()
    assert (user, read_only) == ("kidneylens_app", "on")


# --- Grants survive dbt rebuilding the tables ---------------------------------------

def test_select_still_works_after_dbt_rebuilds_the_tables():
    dbt_dir = ROOT / "dbt" / "kidneylens"
    if not (dbt_dir / "profiles.yml").exists():
        pytest.skip("dbt/kidneylens/profiles.yml not configured")
    proc = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "dbt"), "run", "-q", "--select", "dim_facility_current", "mart_state_services"],
        cwd=dbt_dir, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with connect() as conn:
        for table in PUBLISHED:
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] > 0
