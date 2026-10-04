-- One row per state: home hemodialysis training counts and share for the current snapshot.
-- Definitions are authoritative in docs/metrics.yml; keep this model in sync with it.
--   home_training_share = yes / (yes + no). Unknown (NULL) is excluded from numerator and
--   denominator and reported separately. Denominator 0 -> NULL (never 0, never an error).

with facilities as (

    select * from {{ ref('dim_facility_current') }}

),

counts as (

    select
        state,
        count(*)                                                              as facility_count,
        count(*) filter (where offers_home_hemodialysis_training is true)     as training_yes_count,
        count(*) filter (where offers_home_hemodialysis_training is false)    as training_no_count,
        count(*) filter (where offers_home_hemodialysis_training is null)     as training_unknown_count,
        min(snapshot_id)                                                      as snapshot_id,
        min(downloaded_at)                                                    as downloaded_at
    from facilities
    group by state

)

select
    state,
    facility_count,
    training_yes_count,
    training_no_count,
    training_unknown_count,
    training_yes_count + training_no_count                                    as training_reporting_count,
    -- numeric(7,6): a 0-1 fraction rounded to 6 decimals, so output is predictable
    -- (plain numeric division returns e.g. 0.50000000000000000000 and 0E-20).
    cast(
        training_yes_count::numeric / nullif(training_yes_count + training_no_count, 0)
        as numeric(7, 6)
    )                                                                         as home_training_share,
    snapshot_id,
    downloaded_at
from counts
