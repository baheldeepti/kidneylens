-- One row per CCN, taken from the LATEST VALIDATED snapshot.
--
-- Why not simply MAX(downloaded_at)? That would pick the newest download even if its
-- content is bad (duplicate CCNs, unrecognized values), silently switching every facility
-- to broken data. Instead each snapshot is validated below, and only snapshots that pass
-- are eligible. downloaded_at is then used only to ORDER the eligible snapshots: it is the
-- only reliable ordering we have (the CMS file has no release-date column).
-- Partially loaded snapshots cannot exist: ingestion/load_raw.py loads each one atomically.

with facilities as (

    select * from {{ ref('stg_facilities') }}

),

snapshot_checks as (

    -- One row per snapshot with a count of each kind of problem. Mirrors stg_facilities tests.
    select
        snapshot_id,
        max(downloaded_at)                                          as downloaded_at,
        count(*)                                                    as row_count,
        count(*) filter (where ccn is null)                         as missing_ccn_rows,
        count(ccn) - count(distinct ccn)                            as duplicate_ccn_rows,
        count(*) filter (
            where state is null
               or state not in ('{{ var("valid_states") | join("', '") }}')
        )                                                           as invalid_state_rows,
        count(*) filter (
            where (trim(offers_peritoneal_dialysis_raw) <> '' and offers_peritoneal_dialysis is null)
               or (trim(offers_home_hemodialysis_training_raw) <> '' and offers_home_hemodialysis_training is null)
               or (trim(star_rating_raw) <> '' and star_rating is null)
        )                                                           as unrecognized_value_rows,
        count(*) filter (where star_rating not in (1, 2, 3, 4, 5))  as out_of_range_rating_rows
    from facilities
    group by snapshot_id

),

latest_validated_snapshot as (

    select snapshot_id
    from snapshot_checks
    where row_count > 0
      and missing_ccn_rows = 0
      and duplicate_ccn_rows = 0
      and invalid_state_rows = 0
      and unrecognized_value_rows = 0
      and out_of_range_rating_rows = 0
    order by downloaded_at desc, snapshot_id desc  -- snapshot_id breaks exact ties
    limit 1

)

select
    -- facility explorer + state filter
    f.ccn,
    f.facility_name,
    f.state,
    f.zip_code,

    -- services: true / false / NULL (= unknown; never treat as false)
    f.offers_peritoneal_dialysis,
    f.offers_home_hemodialysis_training,

    -- star rating: NULL when not published; the code explains why
    f.star_rating,
    f.star_rating_availability_code,

    -- source snapshot metadata
    f.snapshot_id,
    f.source_file,
    f.source_url,
    f.source_sha256,
    f.downloaded_at,
    f.loaded_at

from facilities as f
inner join latest_validated_snapshot as s
    on f.snapshot_id = s.snapshot_id
