"""Fixed, parameterized SQL for the Streamlit app.

Rules (see CLAUDE.md "Query safety"):
- Every query is a constant string below. No SQL is ever assembled from user input.
- User choices are validated against an approved list, then passed as query parameters.
- The app connects as `kidneylens_app` (sql/create_app_role.sql): SELECT on the two published
  analytics tables only, in a read-only session.
"""

import os
import re
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

STATE_CODE = re.compile(r"^[A-Z]{2}$")


class InvalidParameter(ValueError):
    """A user-supplied parameter is not on the approved list."""


class Runner(Protocol):
    def __call__(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...


def conninfo_from_env() -> str:
    return psycopg.conninfo.make_conninfo(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "kidneylens"),
        user=os.getenv("APP_DB_USER", "kidneylens_app"),
        password=os.getenv("APP_DB_PASSWORD", "local_app_password"),
        options="-c default_transaction_read_only=on",  # second guard on top of the role setting
        connect_timeout="5",
    )


def run_query(sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Execute one fixed query with bound parameters and return rows as dicts."""
    with psycopg.connect(conninfo_from_env(), row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchall()


# --- SQL (constants only) -----------------------------------------------------

SNAPSHOT_SQL = """
    select snapshot_id, source_url, source_sha256, downloaded_at, count(*) as facility_count
    from analytics.dim_facility_current
    group by snapshot_id, source_url, source_sha256, downloaded_at
"""

STATES_SQL = """
    select state from analytics.mart_state_services order by state
"""

PD_SUMMARY_SQL = """
    select
        count(*) filter (where offers_peritoneal_dialysis is true)  as yes_count,
        count(*) filter (where offers_peritoneal_dialysis is false) as no_count,
        count(*) filter (where offers_peritoneal_dialysis is null)  as unknown_count
    from analytics.dim_facility_current
    where state = %(state)s
"""

PD_FACILITIES_SQL = """
    select ccn, facility_name, city, zip_code, offers_peritoneal_dialysis, star_rating
    from analytics.dim_facility_current
    where state = %(state)s
      and offers_peritoneal_dialysis is not false   -- Yes and Unknown; never treat unknown as No
    order by offers_peritoneal_dialysis nulls last, facility_name, ccn
"""

# Optional filters use two separate fixed queries (all states / one state).
# Python chooses between them; it never adds or removes SQL clauses.

STATE_SERVICES_SQL = """
    select state, facility_count, training_yes_count, training_no_count,
           training_unknown_count, training_reporting_count, home_training_share
    from analytics.mart_state_services
    order by state
"""

STATE_SERVICES_FOR_STATE_SQL = """
    select state, facility_count, training_yes_count, training_no_count,
           training_unknown_count, training_reporting_count, home_training_share
    from analytics.mart_state_services
    where state = %(state)s
"""

STAR_DISTRIBUTION_SQL = """
    select star_rating, count(*) as facility_count
    from analytics.dim_facility_current
    group by star_rating
    order by star_rating nulls last
"""

STAR_DISTRIBUTION_FOR_STATE_SQL = """
    select star_rating, count(*) as facility_count
    from analytics.dim_facility_current
    where state = %(state)s
    group by star_rating
    order by star_rating nulls last
"""


# --- Query functions ----------------------------------------------------------

def validate_state(state: str, approved_states: list[str]) -> str:
    """Return state if it is a 2-letter code on the approved list; otherwise raise."""
    if not isinstance(state, str) or not STATE_CODE.match(state) or state not in approved_states:
        raise InvalidParameter(f"State {state!r} is not an approved state code.")
    return state


def snapshot_info(run: Runner = run_query) -> dict:
    rows = run(SNAPSHOT_SQL)
    if len(rows) != 1:
        raise RuntimeError(f"Expected exactly one current snapshot, found {len(rows)}.")
    return rows[0]


def approved_states(run: Runner = run_query) -> list[str]:
    return [row["state"] for row in run(STATES_SQL)]


def pd_summary(state: str, approved: list[str], run: Runner = run_query) -> dict:
    return run(PD_SUMMARY_SQL, {"state": validate_state(state, approved)})[0]


def pd_facilities(state: str, approved: list[str], run: Runner = run_query) -> list[dict]:
    """Facilities in `state` reporting PD = Yes, plus those with unknown PD status."""
    return run(PD_FACILITIES_SQL, {"state": validate_state(state, approved)})


def state_services(
    state: str | None = None, approved: list[str] | None = None, run: Runner = run_query
) -> list[dict]:
    """Home-training rows for all states, or for one approved state."""
    if state is None:
        return run(STATE_SERVICES_SQL)
    return run(STATE_SERVICES_FOR_STATE_SQL, {"state": validate_state(state, approved or [])})


def star_distribution(
    state: str | None = None, approved: list[str] | None = None, run: Runner = run_query
) -> list[dict]:
    """Star-rating counts (NULL = not rated) for all facilities, or for one approved state."""
    if state is None:
        return run(STAR_DISTRIBUTION_SQL)
    return run(STAR_DISTRIBUTION_FOR_STATE_SQL, {"state": validate_state(state, approved or [])})
