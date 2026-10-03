"""Tests for app/queries.py and app/formatting.py. No database: queries go to a fake runner."""

from decimal import Decimal

import pytest

import queries
from formatting import service_label, share_label, star_label
from queries import InvalidParameter, validate_state

APPROVED = ["AL", "CA", "PR"]


class FakeRunner:
    """Records every (sql, params) call instead of touching a database."""

    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows if rows is not None else [{"yes_count": 1, "no_count": 0, "unknown_count": 0}]

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        return self.rows


# --- Parameter validation -----------------------------------------------------

@pytest.mark.parametrize(
    "bad_state",
    ["XX", "al", "A", "ALA", "", None, 42, "AL'; DROP TABLE x; --", "AL OR 1=1", " AL"],
)
@pytest.mark.parametrize("query_fn", [queries.pd_summary, queries.pd_facilities])
def test_unapproved_state_is_rejected_before_any_sql_runs(query_fn, bad_state):
    run = FakeRunner()
    with pytest.raises(InvalidParameter):
        query_fn(bad_state, APPROVED, run=run)
    assert run.calls == []  # nothing reached the database


def test_valid_code_not_on_approved_list_is_rejected():
    with pytest.raises(InvalidParameter):
        validate_state("TX", APPROVED)  # well-formed, but not approved


def test_approved_state_is_passed_as_a_parameter_not_in_the_sql():
    run = FakeRunner()
    queries.pd_facilities("PR", APPROVED, run=run)
    sql, params = run.calls[0]
    assert sql == queries.PD_FACILITIES_SQL  # the fixed constant, unchanged
    assert params == {"state": "PR"}
    assert "PR" not in sql


def test_all_sql_is_read_only_and_parameterized():
    sql_constants = [v for k, v in vars(queries).items() if k.endswith("_SQL")]
    assert len(sql_constants) == 8
    for sql in sql_constants:
        first_word = sql.split()[0].lower()
        assert first_word == "select"
        assert "{" not in sql and "%s" not in sql  # only named %(param)s placeholders


def test_pd_facilities_query_keeps_unknown_and_excludes_only_explicit_no():
    assert "offers_peritoneal_dialysis is not false" in queries.PD_FACILITIES_SQL


def test_snapshot_info_requires_exactly_one_snapshot():
    with pytest.raises(RuntimeError, match="exactly one"):
        queries.snapshot_info(run=FakeRunner(rows=[{"snapshot_id": "a"}, {"snapshot_id": "b"}]))


# --- Display rules ------------------------------------------------------------

def test_service_label_never_shows_unknown_as_no():
    assert service_label(True) == "Yes"
    assert service_label(False) == "No"
    assert service_label(None) == "Unknown (not reported)"


def test_share_label_null_is_not_available_not_zero():
    assert share_label(None) == "Not available"
    assert share_label(Decimal("0")) == "0.0%"
    assert share_label(Decimal("0.32")) == "32.0%"
    assert share_label(Decimal("0.3333333")) == "33.3%"


def test_star_label_missing_is_not_rated_not_zero():
    assert star_label(None) == "Not rated"
    assert star_label(Decimal("1.0")) == "1 star"
    assert star_label(Decimal("5.0")) == "5 stars"
