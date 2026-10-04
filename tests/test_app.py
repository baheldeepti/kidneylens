"""Smoke test: run the whole Streamlit page against the real database and fail on any error.

Needs PostgreSQL running, dbt models built, and the read-only user created.
"""

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

import queries


@pytest.fixture(scope="module")
def require_app_database():
    try:
        queries.snapshot_info()
    except (psycopg.Error, RuntimeError) as exc:
        pytest.skip(f"App database not ready (docker compose up -d, dbt build, create_app_role.sql): {exc}")


def run_app() -> AppTest:
    return AppTest.from_file("../app/streamlit_app.py", default_timeout=30).run()


def test_app_renders_all_sections_without_errors(require_app_database):
    at = run_app()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    headers = [h.value for h in at.header]
    assert headers[:3] == [
        "1. Facility Explorer: peritoneal dialysis",
        "2. Home hemodialysis training by state",
        "3. Star rating distribution",
    ]


def test_changing_state_updates_facility_explorer(require_app_database):
    at = run_app()
    box = at.selectbox[0]
    other_state = next(s for s in reversed(box.options) if s != box.value)  # works with any loaded data
    box.set_value(other_state).run()
    assert not at.exception, at.exception
    facility_table = at.dataframe[0].value
    assert len(facility_table) > 0
    assert set(facility_table["Offers PD (reported)"]) <= {"Yes", "Unknown (not reported)"}


def test_star_table_has_separate_not_rated_category(require_app_database):
    at = run_app()
    ratings = list(at.dataframe[2].value["Rating"])
    assert ratings[-1] == "Not rated"
    assert "0 stars" not in ratings
