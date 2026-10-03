# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

KidneyLens is a portfolio project that answers a small, fixed set of questions about U.S. dialysis facilities using the public CMS "Dialysis Facility – Listing by Facility" CSV. The user is a beginner: work incrementally, keep each task small, and after every task explain **what changed and how to test it**.

## Stack

- Python: ingestion of the CMS CSV
- PostgreSQL in Docker
- dbt Core with dbt-postgres: transformations and data tests
- Streamlit: frontend
- pytest: Python tests
- GitHub Actions: CI (later)
- Anthropic API: optional, only after the deterministic app works

Never add a technology, library, or service unless the current milestone requires it.

## V1 scope

V1 answers exactly three questions:

1. Which facilities in a selected state report offering peritoneal dialysis?
2. What share of facilities in each state report offering home hemodialysis training?
3. How are published star ratings distributed, including missing ratings?

## Data flow and table grains

CSV snapshot (`data/raw/<snapshot_id>/`, tracked in `data/raw/manifest.json`) → `raw.raw_facilities` (one row per CSV row per snapshot; all 142 source fields in a `record` JSONB column keyed by exact CMS header, values as exact text, blanks as `''`) → dbt models:

| Model | Grain |
|---|---|
| `stg_facilities` | one row per snapshot + CCN (CMS facility ID) |
| `dim_facility_current` | one row per CCN, from the latest validated snapshot |
| `mart_state_services` | one row per state: counts, service share, missingness |

## Data and metric rules

Authoritative metric definitions (formula, grain, missing-value behavior, limitations): `docs/metrics.yml`. Code and UI text must match it.

- CCN and ZIP are always **text**. Never cast them to numbers (leading zeros matter).
- Service flags normalize to boolean `true` / `false`, with **`NULL` meaning unknown** (blank or unrecognized source value). Normalization happens once, in `stg_facilities` (macro `normalize_yes_no`); `_raw` columns keep the exact source text, and a test fails if a non-blank raw value is unrecognized.
- **Never interpret unknown as No.** Unknown is always reported as its own category.
- Missing star ratings stay `NULL`. Do not impute values or replace them with 0.
- Home-training share = facilities reporting Yes ÷ facilities reporting Yes or No. Unknowns are excluded from the denominator and shown separately.
- If the denominator is zero, the share is `NULL`.
- Never describe this metric as patient access or service capacity. It only measures what facilities *report offering*.

## Query safety

- The app connects with a **read-only** database user.
- The app runs only fixed, parameterized SQL that Python selects. No string-built SQL from user input.
- If AI is added: it maps a question to an approved intent and validated parameters only. **AI must never generate SQL in V1.**

## Commands (planned; update as each milestone lands)

```bash
source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d                 # start Postgres
python -m ingestion.download         # download CMS CSV into data/raw/ (no-op if unchanged)
python -m ingestion.load_raw          # load latest snapshot into raw.raw_facilities (no-op if loaded)

pytest                               # all Python tests
pytest tests/test_load_raw.py::test_reloading_same_snapshot_is_a_noop   # single test (DB tests need docker compose up)

cd dbt/kidneylens && cp profiles.yml.example profiles.yml   # once; profiles.yml is gitignored
set -a; source ../../.env; set +a     # dbt reads POSTGRES_PASSWORD from the environment
dbt debug                            # check config + DB connection
dbt build                            # run models + tests (from dbt/kidneylens)
dbt test --select stg_facilities

streamlit run app/main.py
```

## Coding conventions

- Read the CSV with every column as a string. Type and normalize in dbt, not in ingestion.
- Every raw row carries its snapshot date and source file name.
- Keep database settings in environment variables (`.env` is gitignored; `.env.example` is committed).
- Keep modules small and single-purpose. Separate pure logic (CSV parsing, parameter validation) from database I/O so it can be tested without Docker.
- `data/` (downloaded CSVs) and `.venv/` are gitignored.

## Testing expectations

- pytest covers ingestion: CCN/ZIP stay text with leading zeros, blanks are preserved, and unexpected headers fail loudly. Use small hand-made fixture CSVs, not the full CMS file.
- dbt tests cover the grains (uniqueness/not-null on keys), the accepted values `true`/`false`/`unknown`, and the share metric (including null when the denominator is zero).
- Any app query function must have a test showing it rejects parameters that are not approved.

## Out of scope for V1

- Questions beyond the three listed above
- AI-generated or free-form SQL
- Any claims about patient access, quality of care, or capacity
- Imputing missing ratings or service values
- Historical trend analysis across snapshots (the snapshot grain exists, but V1 reports the latest snapshot only)
- Extra infrastructure (orchestrators, cloud deployment, auth, caching layers) and new libraries not needed by the current milestone
