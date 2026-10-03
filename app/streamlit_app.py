"""KidneyLens V1: three fixed questions about CMS dialysis facility data.

Run from the repository root:  streamlit run app/streamlit_app.py
All data access goes through queries.py (fixed SQL, validated parameters, read-only user).
"""

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

import queries
from formatting import UNKNOWN_LABEL, service_label, share_label, star_label

DATASET_PAGE = "https://data.cms.gov/provider-data/dataset/23ew-n7w9"
DATA_DICTIONARY = (
    "https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/dialysis/DF_Data_Dictionary.pdf"
)
METRICS_FILE = Path(__file__).resolve().parent.parent / "docs" / "metrics.yml"
CACHE_SECONDS = 600

st.set_page_config(page_title="KidneyLens", layout="wide")


# --- Cached data access (results cached; SQL lives in queries.py) ---------------

@st.cache_data(ttl=CACHE_SECONDS)
def load_snapshot() -> dict:
    return queries.snapshot_info()


@st.cache_data(ttl=CACHE_SECONDS)
def load_states() -> list[str]:
    return queries.approved_states()


@st.cache_data(ttl=CACHE_SECONDS)
def load_pd(state: str, approved: tuple[str, ...]) -> tuple[dict, list[dict]]:
    return queries.pd_summary(state, list(approved)), queries.pd_facilities(state, list(approved))


@st.cache_data(ttl=CACHE_SECONDS)
def load_state_services() -> list[dict]:
    return queries.state_services()


@st.cache_data(ttl=CACHE_SECONDS)
def load_star_distribution() -> list[dict]:
    return queries.star_distribution()


@st.cache_data
def load_metric_definitions() -> dict:
    metrics = yaml.safe_load(METRICS_FILE.read_text())["metrics"]
    return {m["name"]: m for m in metrics}


# --- Page ---------------------------------------------------------------------

st.title("KidneyLens")
st.caption(
    "What U.S. dialysis facilities **report** to CMS about the services they offer. "
    "Facility-level public data; no patient-level data."
)

try:
    snapshot = load_snapshot()
    states = load_states()
except Exception as exc:  # show a clear message instead of a stack trace
    st.error(
        "Could not load data from PostgreSQL. Check that the database is running "
        "(`docker compose up -d`), the dbt models are built (`dbt build`), and the read-only "
        f"role exists (`sql/create_app_role.sql`).\n\nDetails: {exc}"
    )
    st.stop()

st.info(
    f"**Source:** CMS *Dialysis Facility – Listing by Facility* ([dataset page]({DATASET_PAGE}))  \n"
    f"**Snapshot:** `{snapshot['snapshot_id']}`, downloaded "
    f"{snapshot['downloaded_at']:%Y-%m-%d} (UTC), {snapshot['facility_count']:,} facilities. "
    "The download date is not the clinical reporting period."
)

# 1. Facility Explorer ----------------------------------------------------------
st.header("1. Facility Explorer: peritoneal dialysis")
st.write("Which facilities in a selected state report offering peritoneal dialysis (PD)?")

state = st.selectbox("State", states, index=states.index("AL") if "AL" in states else 0)
summary, facilities = load_pd(state, tuple(states))

c1, c2, c3 = st.columns(3)
c1.metric("Report offering PD", f"{summary['yes_count']:,}")
c2.metric("Report not offering PD", f"{summary['no_count']:,}")
c3.metric("PD status unknown", f"{summary['unknown_count']:,}")

if facilities:
    table = pd.DataFrame(
        {
            "Facility": [f["facility_name"] for f in facilities],
            "CCN": [f["ccn"] for f in facilities],
            "City": [f["city"] or "—" for f in facilities],
            "ZIP": [f["zip_code"] or "—" for f in facilities],
            "Offers PD (reported)": [service_label(f["offers_peritoneal_dialysis"]) for f in facilities],
        }
    )
    st.dataframe(table, hide_index=True, width="stretch")
else:
    st.write(f"No facilities in {state} report offering PD.")
st.caption(
    f"Listed: facilities reporting **Yes**, plus any with **{UNKNOWN_LABEL}**, shown so missing "
    "information is visible. Unknown is not treated as No. A reported service does not show "
    "current capacity or whether the facility is accepting patients."
)

# 2. Home Training by State ---------------------------------------------------
st.header("2. Home hemodialysis training by state")
st.write("What share of facilities in each state report offering home hemodialysis training?")

rows = load_state_services()
st.dataframe(
    pd.DataFrame(
        {
            "State": [r["state"] for r in rows],
            "Home-training share": [share_label(r["home_training_share"]) for r in rows],
            "Yes": [r["training_yes_count"] for r in rows],
            "No": [r["training_no_count"] for r in rows],
            "Unknown": [r["training_unknown_count"] for r in rows],
            "Reporting (Yes + No)": [r["training_reporting_count"] for r in rows],
            "All facilities": [r["facility_count"] for r in rows],
        }
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Share = Yes ÷ (Yes + No). Unknown is excluded from the share and shown in its own column. "
    "'Not available' means no facility in the state reported Yes or No. States with few "
    "facilities can swing widely; compare the share with the 'Reporting' count."
)

# 3. Star Rating Distribution -------------------------------------------------
st.header("3. Star rating distribution")
st.write("How are published star ratings distributed, including missing ratings?")

stars = load_star_distribution()
total = sum(r["facility_count"] for r in stars)
star_table = pd.DataFrame(
    {
        "Rating": [star_label(r["star_rating"]) for r in stars],
        "Facilities": [r["facility_count"] for r in stars],
        "Percent of all facilities": [f"{r['facility_count'] / total * 100:.1f}%" for r in stars],
    }
)
left, right = st.columns([3, 2])
left.bar_chart(star_table, x="Rating", y="Facilities", sort=False)
right.dataframe(star_table, hide_index=True, width="stretch")
st.caption(
    "'Not rated' means CMS did not publish a rating for the facility; it is not a zero. "
    f"Reasons are given by CMS availability codes ([data dictionary]({DATA_DICTIONARY}))."
)

# About the data -----------------------------------------------------------------
st.header("About this data")

with st.expander("Metric definitions", expanded=False):
    for name, m in load_metric_definitions().items():
        st.markdown(f"**`{name}`**: {m['description']}")
        st.markdown(f"- Numerator: {m['numerator']}  \n- Denominator: {m['denominator']}")
        st.markdown(f"- Missing values: {m['missing_value_behavior']}")
    st.caption("Source of truth: docs/metrics.yml")

with st.expander("Limitations", expanded=True):
    for item in load_metric_definitions()["home_training_share"]["interpretation_limitations"]:
        st.markdown(f"- {item}")
    st.markdown(
        "- Facility data does not determine treatment suitability; that is a clinical decision.\n"
        "- Missing does not mean No. Unknown values are always shown separately.\n"
        "- This is facility-level public information. There is no patient-level data.\n"
        f"- Field meanings: [CMS data dictionary]({DATA_DICTIONARY}). Full context: docs/context.md."
    )
