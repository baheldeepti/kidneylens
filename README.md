# KidneyLens

A small analytics project built on the public CMS **Dialysis Facility – Listing by Facility** dataset. It answers three questions:

1. Which facilities in a selected state report offering peritoneal dialysis?
2. What share of facilities in each state report offering home hemodialysis training?
3. How are published star ratings distributed, including missing ratings?

> These figures describe what facilities **report offering** to CMS. They are not measures of patient access or service capacity.

## Stack

Python (ingestion) · PostgreSQL in Docker · dbt Core + dbt-postgres · Streamlit · pytest

## Status

Early setup. No application code yet; this README will grow as each milestone is built.

## Setup

Requires Python 3.11+ and Docker.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit the passwords
```

## How metrics are calculated

- Service values are normalized to `true`, `false`, or `unknown`. Unknown is never counted as No.
- Home hemodialysis training share = facilities reporting Yes ÷ facilities reporting Yes or No. Unknowns are shown separately; if there are no Yes/No reports the share is empty (null).
- Missing star ratings stay missing; they are not counted as zero.

## Data source

Centers for Medicare & Medicaid Services (CMS), Dialysis Facility – Listing by Facility (public data).
