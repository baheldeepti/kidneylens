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


def pd_counts(at: AppTest) -> tuple[int, int]:
    """(report offering PD, PD status unknown) from the Facility Explorer metrics."""
    metrics = {m.label: int(m.value.replace(",", "")) for m in at.metric}
    return metrics["Report offering PD"], metrics["PD status unknown"]


def test_changing_state_updates_facility_explorer(require_app_database):
    """Works with whatever data is loaded: checks the first and last state in the dropdown,
    covering both a state with PD facilities and (in the CI sample) one with none."""
    at = run_app()
    options = at.selectbox[0].options
    for state in dict.fromkeys([options[0], options[-1]]):
        at.selectbox[0].set_value(state).run()
        assert not at.exception, at.exception
        yes, unknown = pd_counts(at)
        tables = [d.value for d in at.dataframe if "Offers PD (reported)" in d.value.columns]
        if yes + unknown:
            assert len(tables) == 1 and len(tables[0]) == yes + unknown, state
            assert set(tables[0]["Offers PD (reported)"]) <= {"Yes", "Unknown (not reported)"}
        else:
            assert tables == [], state
            assert any(f"No facilities in {state} report offering PD." in m.value for m in at.markdown)


def test_star_table_has_separate_not_rated_category(require_app_database):
    at = run_app()
    star_table = next(d.value for d in at.dataframe if "Rating" in d.value.columns)  # by content, not position
    ratings = list(star_table["Rating"])
    assert ratings[-1] == "Not rated"
    assert "0 stars" not in ratings
