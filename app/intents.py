"""Deterministic intent layer: the ONLY way a question can reach the database.

A request is a plain dict, e.g. {"intent": "HOME_HD_TRAINING_SHARE", "params": {"state": "CA"}}.
Later an LLM may produce this dict; it is treated as untrusted input either way.

parse_request() validates it against the approved intents below. Anything unexpected raises
an IntentError BEFORE any SQL runs. run_intent() then calls a function in queries.py, which
executes predefined, parameterized SQL. No SQL is ever built from request text.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import queries
from queries import InvalidParameter, Runner, run_query


class Intent(str, Enum):
    FIND_PD_FACILITIES = "FIND_PD_FACILITIES"
    HOME_HD_TRAINING_SHARE = "HOME_HD_TRAINING_SHARE"
    STAR_RATING_DISTRIBUTION = "STAR_RATING_DISTRIBUTION"


class IntentError(Exception):
    """Base class: the request was refused and no query was run. Message is safe to show users."""


class UnsupportedIntent(IntentError):
    pass


class InvalidIntentParameters(IntentError):
    pass


@dataclass(frozen=True)
class IntentSpec:
    required: frozenset[str]
    optional: frozenset[str]
    description: str


# The complete list of approved intents and their parameters. Nothing else is accepted.
SPECS: dict[Intent, IntentSpec] = {
    Intent.FIND_PD_FACILITIES: IntentSpec(
        required=frozenset({"state"}),
        optional=frozenset(),
        description="Facilities in a state that report offering peritoneal dialysis (plus unknowns).",
    ),
    Intent.HOME_HD_TRAINING_SHARE: IntentSpec(
        required=frozenset(),
        optional=frozenset({"state"}),
        description="Share of facilities reporting home hemodialysis training, by state.",
    ),
    Intent.STAR_RATING_DISTRIBUTION: IntentSpec(
        required=frozenset(),
        optional=frozenset({"state"}),
        description="Distribution of published star ratings, with missing ratings as a category.",
    ),
}

ALLOWED_REQUEST_KEYS = {"intent", "params"}


@dataclass(frozen=True)
class ValidatedRequest:
    intent: Intent
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    params: dict[str, str]
    data: dict[str, Any]


def _normalize_state(value: Any, approved_states: list[str]) -> str:
    if not isinstance(value, str):
        raise InvalidIntentParameters("Parameter 'state' must be a two-letter state code.")
    candidate = value.strip().upper()
    try:
        return queries.validate_state(candidate, approved_states)
    except InvalidParameter:
        raise InvalidIntentParameters(f"{value!r} is not a supported state code.") from None


def parse_request(raw: Any, approved_states: list[str]) -> ValidatedRequest:
    """Validate an untrusted request. Raises IntentError; never touches the database."""
    if not isinstance(raw, dict):
        raise UnsupportedIntent("Request must be an object with 'intent' and optional 'params'.")
    extra_keys = set(raw) - ALLOWED_REQUEST_KEYS
    if extra_keys:
        raise UnsupportedIntent(f"Unexpected request fields: {sorted(extra_keys)}.")

    name = raw.get("intent")
    try:
        intent = Intent(name)  # exact match only; no case-folding or fuzzy matching
    except ValueError:
        supported = ", ".join(i.value for i in Intent)
        raise UnsupportedIntent(f"Unsupported question type {name!r}. Supported: {supported}.") from None

    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise InvalidIntentParameters("'params' must be an object.")

    spec = SPECS[intent]
    unknown = set(params) - spec.required - spec.optional
    if unknown:
        raise InvalidIntentParameters(f"{intent.value} does not accept: {sorted(unknown)}.")
    missing = spec.required - {k for k, v in params.items() if v not in (None, "")}
    if missing:
        raise InvalidIntentParameters(f"{intent.value} requires: {sorted(missing)}.")

    clean: dict[str, str] = {}
    if params.get("state") not in (None, ""):
        clean["state"] = _normalize_state(params["state"], approved_states)
    return ValidatedRequest(intent=intent, params=clean)


# --- Execution: one fixed handler per intent ------------------------------------

def _find_pd_facilities(params: dict, approved: list[str], run: Runner) -> dict:
    state = params["state"]
    return {
        "summary": queries.pd_summary(state, approved, run=run),
        "facilities": queries.pd_facilities(state, approved, run=run),
    }


def _home_hd_training_share(params: dict, approved: list[str], run: Runner) -> dict:
    return {"rows": queries.state_services(params.get("state"), approved, run=run)}


def _star_rating_distribution(params: dict, approved: list[str], run: Runner) -> dict:
    return {"rows": queries.star_distribution(params.get("state"), approved, run=run)}


HANDLERS: dict[Intent, Callable[[dict, list[str], Runner], dict]] = {
    Intent.FIND_PD_FACILITIES: _find_pd_facilities,
    Intent.HOME_HD_TRAINING_SHARE: _home_hd_training_share,
    Intent.STAR_RATING_DISTRIBUTION: _star_rating_distribution,
}


def run_intent(raw: Any, approved_states: list[str] | None = None, run: Runner = run_query) -> IntentResult:
    """Validate `raw` and run its predefined queries. Raises IntentError on any invalid request."""
    if approved_states is None:
        approved_states = queries.approved_states(run=run)
    request = parse_request(raw, approved_states)
    data = HANDLERS[request.intent](request.params, approved_states, run)
    return IntentResult(intent=request.intent, params=request.params, data=data)
