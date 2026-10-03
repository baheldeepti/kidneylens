"""Tests for app/intents.py. Unit tests use a fake runner; the last section uses the real database."""

import psycopg
import pytest

import intents
import queries
from intents import Intent, IntentError, InvalidIntentParameters, UnsupportedIntent, run_intent

APPROVED = ["AL", "CA", "PR"]


class FakeRunner:
    """Records (sql, params) and returns canned rows shaped like the real query results."""

    def __init__(self):
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if sql == queries.PD_SUMMARY_SQL:
            return [{"yes_count": 2, "no_count": 1, "unknown_count": 1}]
        return [{"row": 1}]

    @property
    def sql_run(self):
        return [sql for sql, _ in self.calls]


def run(request, runner=None):
    runner = runner or FakeRunner()
    return run_intent(request, approved_states=APPROVED, run=runner), runner


def assert_refused(request, error=IntentError):
    runner = FakeRunner()
    with pytest.raises(error):
        run_intent(request, approved_states=APPROVED, run=runner)
    assert runner.calls == [], "a refused request must not run any SQL"


# --- FIND_PD_FACILITIES (state required) --------------------------------------

def test_find_pd_runs_fixed_queries_with_state_parameter():
    result, runner = run({"intent": "FIND_PD_FACILITIES", "params": {"state": "AL"}})
    assert result.intent is Intent.FIND_PD_FACILITIES
    assert result.params == {"state": "AL"}
    assert runner.calls == [
        (queries.PD_SUMMARY_SQL, {"state": "AL"}),
        (queries.PD_FACILITIES_SQL, {"state": "AL"}),
    ]
    assert result.data["summary"]["unknown_count"] == 1  # unknowns are reported, not dropped


def test_find_pd_normalizes_state_case_and_spaces():
    result, _ = run({"intent": "FIND_PD_FACILITIES", "params": {"state": " pr "}})
    assert result.params == {"state": "PR"}


@pytest.mark.parametrize("params", [None, {}, {"state": None}, {"state": ""}])
def test_find_pd_requires_state(params):
    assert_refused({"intent": "FIND_PD_FACILITIES", "params": params}, InvalidIntentParameters)


# --- HOME_HD_TRAINING_SHARE (state optional) ----------------------------------

def test_home_training_all_states_uses_unfiltered_query():
    result, runner = run({"intent": "HOME_HD_TRAINING_SHARE"})
    assert runner.calls == [(queries.STATE_SERVICES_SQL, None)]
    assert result.params == {}


def test_home_training_one_state_uses_filtered_query():
    result, runner = run({"intent": "HOME_HD_TRAINING_SHARE", "params": {"state": "ca"}})
    assert runner.calls == [(queries.STATE_SERVICES_FOR_STATE_SQL, {"state": "CA"})]


# --- STAR_RATING_DISTRIBUTION (state optional) --------------------------------

def test_star_distribution_all_states_uses_unfiltered_query():
    _, runner = run({"intent": "STAR_RATING_DISTRIBUTION", "params": {}})
    assert runner.calls == [(queries.STAR_DISTRIBUTION_SQL, None)]


def test_star_distribution_one_state_uses_filtered_query():
    _, runner = run({"intent": "STAR_RATING_DISTRIBUTION", "params": {"state": "AL"}})
    assert runner.calls == [(queries.STAR_DISTRIBUTION_FOR_STATE_SQL, {"state": "AL"})]


# --- Invalid parameters (all intents) -----------------------------------------

@pytest.mark.parametrize("intent", [i.value for i in Intent])
@pytest.mark.parametrize(
    "state",
    ["XX", "TX", "Alabama", "A", 42, ["AL"], {"code": "AL"}, "AL'; DROP TABLE x; --", "AL OR 1=1"],
)
def test_bad_state_is_refused_for_every_intent(intent, state):
    assert_refused({"intent": intent, "params": {"state": state}}, InvalidIntentParameters)


@pytest.mark.parametrize("intent", [i.value for i in Intent])
@pytest.mark.parametrize("extra", [{"sql": "select 1"}, {"limit": 5}, {"zip": "35233"}])
def test_unexpected_parameters_are_refused(intent, extra):
    assert_refused({"intent": intent, "params": {"state": "AL", **extra}}, InvalidIntentParameters)


def test_params_must_be_an_object():
    assert_refused({"intent": "HOME_HD_TRAINING_SHARE", "params": "state=AL"}, InvalidIntentParameters)


# --- Unsupported intents fail safely ------------------------------------------

@pytest.mark.parametrize(
    "request_",
    [
        {"intent": "DELETE_FACILITIES"},
        {"intent": "find_pd_facilities", "params": {"state": "AL"}},  # exact names only
        {"intent": "SELECT * FROM raw.raw_facilities"},
        {"intent": None},
        {"intent": 1},
        {},
        {"intent": "HOME_HD_TRAINING_SHARE", "sql": "drop table x"},  # extra top-level field
        "HOME_HD_TRAINING_SHARE",
        None,
        ["FIND_PD_FACILITIES"],
    ],
)
def test_unsupported_requests_are_refused_without_running_sql(request_):
    assert_refused(request_, UnsupportedIntent)


def test_error_messages_are_safe_to_show():
    with pytest.raises(UnsupportedIntent) as exc:
        run_intent({"intent": "DROP_TABLES"}, approved_states=APPROVED, run=FakeRunner())
    assert "Supported:" in str(exc.value) and "FIND_PD_FACILITIES" in str(exc.value)


# --- Registry integrity ---------------------------------------------------------

def test_exactly_three_approved_intents_each_with_spec_and_handler():
    assert {i.value for i in Intent} == {
        "FIND_PD_FACILITIES",
        "HOME_HD_TRAINING_SHARE",
        "STAR_RATING_DISTRIBUTION",
    }
    assert set(intents.SPECS) == set(Intent) == set(intents.HANDLERS)


def test_every_query_an_intent_can_run_is_a_predefined_constant():
    predefined = {v for k, v in vars(queries).items() if k.endswith("_SQL")}
    requests = [
        {"intent": "FIND_PD_FACILITIES", "params": {"state": "AL"}},
        {"intent": "HOME_HD_TRAINING_SHARE"},
        {"intent": "HOME_HD_TRAINING_SHARE", "params": {"state": "AL"}},
        {"intent": "STAR_RATING_DISTRIBUTION"},
        {"intent": "STAR_RATING_DISTRIBUTION", "params": {"state": "AL"}},
    ]
    for request in requests:
        _, runner = run(request)
        assert set(runner.sql_run) <= predefined


def test_approved_states_are_loaded_from_database_when_not_given():
    runner = FakeRunner()
    runner_rows = {queries.STATES_SQL: [{"state": "AL"}]}
    calls = []

    def fake(sql, params=None):
        calls.append(sql)
        return runner_rows.get(sql) or runner(sql, params)

    run_intent({"intent": "HOME_HD_TRAINING_SHARE", "params": {"state": "AL"}}, run=fake)
    assert calls[0] == queries.STATES_SQL


# --- Against the real database (skipped if not available) -----------------------

@pytest.fixture(scope="module")
def live_states():
    try:
        return queries.approved_states()
    except psycopg.Error as exc:
        pytest.skip(f"App database not ready: {exc}")


def test_live_all_three_intents(live_states):
    pd = run_intent({"intent": "FIND_PD_FACILITIES", "params": {"state": "AL"}})
    s = pd.data["summary"]
    assert len(pd.data["facilities"]) == s["yes_count"] + s["unknown_count"]
    assert all(f["offers_peritoneal_dialysis"] is not False for f in pd.data["facilities"])

    all_states = run_intent({"intent": "HOME_HD_TRAINING_SHARE"}).data["rows"]
    al = run_intent({"intent": "HOME_HD_TRAINING_SHARE", "params": {"state": "AL"}}).data["rows"]
    assert len(all_states) == len(live_states)
    assert al == [r for r in all_states if r["state"] == "AL"]

    stars_al = run_intent({"intent": "STAR_RATING_DISTRIBUTION", "params": {"state": "AL"}}).data["rows"]
    assert sum(r["facility_count"] for r in stars_al) == al[0]["facility_count"]
    assert stars_al[-1]["star_rating"] is None  # 'not rated' kept as its own category
