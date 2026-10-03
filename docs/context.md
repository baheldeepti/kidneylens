# KidneyLens data context

## Dataset

**CMS "Dialysis Facility – Listing by Facility"** (dataset ID `23ew-n7w9`), published by the Centers for Medicare & Medicaid Services. CMS describes it as "a list of all dialysis facilities registered with Medicare that includes addresses and phone numbers, as well as services and quality of care provided." The data dictionary is produced by the University of Michigan Kidney Epidemiology and Cost Center.

- One row per facility, identified by CMS Certification Number (CCN).
- The 2026-06-16 release has 7,490 facilities and 142 columns. KidneyLens V1 models 8 of them.

**This is facility-level public information. There is no patient-level data.** KidneyLens never sees, stores, or infers anything about individual patients.

## Sources

| What | Where |
|---|---|
| Dataset page | https://data.cms.gov/provider-data/dataset/23ew-n7w9 |
| CSV download | URL changes with each CMS release; current one is `DEFAULT_URL` in `ingestion/download.py` |
| Data dictionary | https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/dialysis/DF_Data_Dictionary.pdf (edition checked: April 2026) |
| CMS contact | Dialysis Facility Helpdesk, DialysisData@umich.edu |

## Snapshots and dates

`python -m ingestion.download` saves each new file unchanged under `data/raw/<snapshot_id>/` and records it in `data/raw/manifest.json` (source URL, UTC download time, SHA-256, row count). Re-downloading an identical file is a no-op.

| Snapshot | CMS release (metadata "modified") | Downloaded (UTC) | Rows |
|---|---|---|---|
| `20261003T053020Z` | 2026-06-16 | 2026-10-03 05:30 | 7,490 |

There are three different kinds of date, and they must not be confused:

- **Download date:** when KidneyLens fetched the file.
- **Release date:** when CMS published it.
- **Reporting period:** the period the data describes. It is set per measure in the file; for example, `Five Star Date` is `01Jan2021-31Dec2024` for every facility in this snapshot.

**The download date is not necessarily the clinical reporting period.** Data downloaded in October 2026 can describe care from years earlier.

## Fields used in V1

| CMS column | Dictionary description | KidneyLens column |
|---|---|---|
| CMS Certification Number (CCN) | Facility identifier | `ccn` (text; leading zeros kept) |
| Facility Name | — | `facility_name` |
| State | — | `state` (50 states, DC, PR, GU, VI, AS, MP) |
| ZIP Code | — | `zip_code` (text) |
| Offers peritoneal dialysis | "Whether the facility offers peritoneal dialysis" | `offers_peritoneal_dialysis` |
| Offers home hemodialysis training | "Whether the facility offers home hemodialysis training" | `offers_home_hemodialysis_training` |
| Five Star | "The quality of care star rating for the facility" | `star_rating` |
| Five Star Data Availability Code | Whether rating data was available, or why not | `star_rating_availability_code` |
| Five Star Date | "The data collection period for the quality of care star rating" | not yet modeled |

## Service flags

The two service fields record what a facility **reports** to CMS: `Yes` or `No`. KidneyLens maps them to `true` (Yes), `false` (No), or `NULL` (unknown: blank or unrecognized). In the current snapshot every facility has an explicit Yes or No.

**A reported service does not establish current service capacity.** "Yes" means the facility reports offering the service. It does not say whether the facility has openings, how many patients it serves, or whether the service is available today.

## Missing values

- **Missing does not mean No.** An unknown service value is never counted as "No"; it is reported separately as unknown.
- **A missing star rating stays missing.** It is never treated as 0 or averaged in. In the current snapshot, 491 facilities have no rating; each has an availability code explaining why:

| Code | Facilities | Meaning |
|---|---|---|
| `001` | 6,999 (rated) | Data available |
| `201` | 5 | "Data not reported" (dictionary) |
| `260` | 415 | **Not confirmed.** Not found in the dictionary text; look it up before showing it to users |
| `258` | 71 | **Not confirmed.** As above |

The dictionary's star-rating footnotes include "not enough quality measure data to calculate a star rating", "at least one measure … was not accurate", and "data suppressed … natural disaster". Codes 258 and 260 likely correspond to some of these, but that mapping has not been verified.

## Limitations

- **Facility data does not determine treatment suitability.** Nothing here says which dialysis type is right for any person; that is a clinical decision.
- Values are self-reported to or calculated by CMS; KidneyLens does not verify them.
- Each snapshot reflects one CMS release; facilities open, close, and change services between releases.
- Metrics count facilities equally. They are not weighted by patients, stations, or population.
- States with few facilities produce unstable shares (the smallest has 2 facilities).
- Metric definitions and their specific limitations: see `docs/metrics.yml`.
