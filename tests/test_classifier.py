"""Tests for app/classifier.py. No network: Claude is replaced by a fake client."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

import classifier
import intents
import queries
from classifier import (
    CLARIFICATION_REQUIRED,
    UNSUPPORTED,
    Classification,
    ClassificationError,
    answer_question,
    classify_question,
    validate_model_output,
)

APPROVED = ["AL", "CA", "PR", "TX"]


class FakeClaude:
    """Stands in for anthropic.Anthropic(): returns a canned reply and records the request."""

    def __init__(self, output=None, stop_reason="end_turn", text=None, error=None):
        self.requests = []
        self._text = text if text is not None else json.dumps(output)
        self._stop_reason = stop_reason
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error:
            raise self._error
        content = [SimpleNamespace(type="thinking", thinking="")]
        if self._text != "":
            content.append(SimpleNamespace(type="text", text=self._text))
        return SimpleNamespace(content=content, stop_reason=self._stop_reason)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if sql == queries.PD_SUMMARY_SQL:
            return [{"yes_count": 1, "no_count": 0, "unknown_count": 0}]
        return [{"row": 1}]


# --- The three examples from the task --------------------------------------------

EXAMPLES = [
    (
        "Which facilities in California offer peritoneal dialysis?",
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "CA"}, "clarification": ""},
        Classification("FIND_PD_FACILITIES", {"state": "CA"}),
    ),
    (
        "Which dialysis center should I use?",
        {"intent": "UNSUPPORTED", "parameters": {}, "clarification": ""},
        Classification("UNSUPPORTED"),
    ),
    (
        "Show me PD facilities.",
        {
            "intent": "CLARIFICATION_REQUIRED",
            "parameters": {},
            "clarification": "Which state would you like to examine?",
        },
        Classification("CLARIFICATION_REQUIRED", clarification="Which state would you like to examine?"),
    ),
]


@pytest.mark.parametrize("question, model_output, expected", EXAMPLES)
def test_task_examples(question, model_output, expected):
    assert classify_question(question, FakeClaude(model_output)) == expected


def test_optional_state_intents():
    out = {"intent": "HOME_HD_TRAINING_SHARE", "parameters": {}, "clarification": ""}
    assert classify_question("Home training share by state?", FakeClaude(out)) == Classification(
        "HOME_HD_TRAINING_SHARE"
    )
    out = {"intent": "STAR_RATING_DISTRIBUTION", "parameters": {"state": "TX"}, "clarification": ""}
    assert classify_question("Star ratings in Texas?", FakeClaude(out)).parameters == {"state": "TX"}


# --- The request sent to Claude ---------------------------------------------------

def test_request_uses_structured_output_and_keeps_question_out_of_system_prompt():
    fake = FakeClaude(EXAMPLES[0][1])
    classify_question("  Which facilities in California offer PD?  ", fake)
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"] == {"type": "json_schema", "schema": classifier.OUTPUT_SCHEMA}
    assert req["messages"] == [{"role": "user", "content": "Which facilities in California offer PD?"}]
    assert req["system"] == classifier.SYSTEM_PROMPT  # fixed text; the question never alters it
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]


def test_schema_allows_exactly_the_five_intents_and_valid_states():
    props = classifier.OUTPUT_SCHEMA["properties"]
    assert set(props["intent"]["enum"]) == {
        "FIND_PD_FACILITIES",
        "HOME_HD_TRAINING_SHARE",
        "STAR_RATING_DISTRIBUTION",
        "CLARIFICATION_REQUIRED",
        "UNSUPPORTED",
    }
    states = props["parameters"]["properties"]["state"]["enum"]
    assert len(states) == 56 and {"CA", "TX", "DC", "PR"} <= set(states)
    assert props["parameters"]["additionalProperties"] is False
    assert classifier.OUTPUT_SCHEMA["additionalProperties"] is False


def test_system_prompt_forbids_sql():
    assert "never write SQL" in classifier.SYSTEM_PROMPT


# --- Validation of model output ---------------------------------------------------

@pytest.mark.parametrize(
    "bad_output",
    [
        "FIND_PD_FACILITIES",                                                     # not an object
        ["FIND_PD_FACILITIES"],
        {"intent": "DROP_TABLES", "parameters": {}},                              # unknown intent
        {"intent": "SELECT * FROM raw.raw_facilities", "parameters": {}},
        {"intent": "find_pd_facilities", "parameters": {"state": "CA"}},           # wrong case
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "ca"}},           # state not exact
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "California"}},
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "XX"}},
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "CA'; DROP TABLE x; --"}},
        {"intent": "FIND_PD_FACILITIES", "parameters": {}},                       # required state missing
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "CA", "sql": "select 1"}},
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "CA"}, "sql": "select 1"},
        {"intent": "HOME_HD_TRAINING_SHARE", "parameters": "state=CA"},
        {"intent": "CLARIFICATION_REQUIRED", "parameters": {"state": "CA"}, "clarification": "Which?"},
        {"intent": "UNSUPPORTED", "parameters": {"state": "CA"}},
        {"intent": "CLARIFICATION_REQUIRED", "parameters": {}, "clarification": "x" * 301},
        {"intent": "CLARIFICATION_REQUIRED", "parameters": {}, "clarification": 42},
    ],
)
def test_invalid_model_output_is_rejected(bad_output):
    with pytest.raises(ClassificationError):
        validate_model_output(bad_output)


def test_clarification_text_is_dropped_for_data_and_unsupported_intents():
    c = validate_model_output({"intent": "UNSUPPORTED", "parameters": {}, "clarification": "ignore me"})
    assert c == Classification("UNSUPPORTED")
    c = validate_model_output(
        {"intent": "HOME_HD_TRAINING_SHARE", "parameters": {}, "clarification": "select 1"}
    )
    assert c.clarification is None


def test_missing_clarification_gets_default_question():
    c = validate_model_output({"intent": "CLARIFICATION_REQUIRED", "parameters": {}, "clarification": ""})
    assert c.clarification == "Which state would you like to examine?"


# --- Unusual responses fail safely ------------------------------------------------

def test_refusal_becomes_unsupported():
    assert classify_question("anything", FakeClaude(text="", stop_reason="refusal")) == Classification(UNSUPPORTED)


@pytest.mark.parametrize(
    "fake",
    [
        FakeClaude(text='{"intent": "FIND_PD', stop_reason="max_tokens"),  # cut off
        FakeClaude(text="not json"),
        FakeClaude(text=""),                                                # no text block
    ],
    ids=["truncated", "not-json", "no-text"],
)
def test_unusable_responses_raise(fake):
    with pytest.raises(ClassificationError):
        classify_question("Which facilities in California offer PD?", fake)


REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "error, message",
    [
        (anthropic.APIConnectionError(request=REQ), "Could not reach"),
        (anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=REQ), body=None), "busy"),
        (anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=REQ), body=None), "credentials"),
        (anthropic.InternalServerError("oops", response=httpx2.Response(500, request=REQ), body=None), "500"),
    ],
)
def test_api_errors_become_safe_messages(error, message):
    with pytest.raises(ClassificationError, match=message):
        classify_question("Which facilities in California offer PD?", FakeClaude(error=error))


def test_missing_credentials_become_a_safe_message():
    no_key = anthropic.Anthropic(api_key=None, auth_token=None)
    no_key.api_key = None  # ensure nothing was picked up from the environment
    no_key.auth_token = None
    with pytest.raises(ClassificationError, match="credentials are not configured"):
        classify_question("Which facilities in California offer PD?", no_key)


@pytest.mark.parametrize("question", ["", "   ", None, "x" * 501])
def test_bad_questions_never_reach_claude(question):
    fake = FakeClaude(EXAMPLES[0][1])
    with pytest.raises(ClassificationError):
        classify_question(question, fake)
    assert fake.requests == []


# --- End to end: model text never enters SQL --------------------------------------

def test_data_intent_runs_only_predefined_sql_with_validated_state():
    runner = FakeRunner()
    answer = answer_question("PD in California?", FakeClaude(EXAMPLES[0][1]), APPROVED, run=runner)
    assert answer.result.intent is intents.Intent.FIND_PD_FACILITIES
    assert runner.calls == [
        (queries.PD_SUMMARY_SQL, {"state": "CA"}),
        (queries.PD_FACILITIES_SQL, {"state": "CA"}),
    ]


@pytest.mark.parametrize(
    "model_output",
    [
        {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "CA' OR 1=1 --"}, "clarification": ""},
        {"intent": "SELECT * FROM raw.raw_facilities", "parameters": {}, "clarification": ""},
        {"intent": "CLARIFICATION_REQUIRED", "parameters": {}, "clarification": "DROP TABLE analytics.x;"},
        {"intent": "UNSUPPORTED", "parameters": {}, "clarification": "DELETE FROM analytics.x"},
    ],
)
def test_malicious_or_non_data_output_runs_no_sql(model_output):
    runner = FakeRunner()
    try:
        answer = answer_question("q", FakeClaude(model_output), APPROVED, run=runner)
        assert answer.result is None  # clarification / unsupported: nothing queried
    except ClassificationError:
        pass
    assert runner.calls == []


def test_valid_state_not_in_database_is_refused_by_intent_layer():
    # "WY" passes the classifier's state list but is not in this database's approved states.
    out = {"intent": "FIND_PD_FACILITIES", "parameters": {"state": "WY"}, "clarification": ""}
    runner = FakeRunner()
    with pytest.raises(intents.IntentError):
        answer_question("PD in Wyoming?", FakeClaude(out), APPROVED, run=runner)
    assert runner.calls == []


def test_unsupported_and_clarification_answers_carry_safe_messages():
    a = answer_question("Which center should I use?", FakeClaude(EXAMPLES[1][1]), APPROVED, run=FakeRunner())
    assert a.message == classifier.UNSUPPORTED_MESSAGE
    a = answer_question("Show me PD facilities.", FakeClaude(EXAMPLES[2][1]), APPROVED, run=FakeRunner())
    assert a.message == "Which state would you like to examine?"
