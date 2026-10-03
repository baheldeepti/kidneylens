-- One row per snapshot_id + CCN. Typed and normalized V1 fields from the raw CMS file.
-- Unknown service values are NULL (never false). Missing star ratings are NULL (never 0).

with source as (

    select * from {{ source('raw', 'raw_facilities') }}

),

extracted as (

    select
        snapshot_id,
        record ->> 'CMS Certification Number (CCN)'      as ccn_raw,
        record ->> 'Facility Name'                       as facility_name_raw,
        record ->> 'City/Town'                           as city_raw,
        record ->> 'State'                               as state_raw,
        record ->> 'ZIP Code'                            as zip_code_raw,
        record ->> 'Offers peritoneal dialysis'          as offers_peritoneal_dialysis_raw,
        record ->> 'Offers home hemodialysis training'   as offers_home_hemodialysis_training_raw,
        record ->> 'Five Star'                           as star_rating_raw,
        record ->> 'Five Star Data Availability Code'    as star_rating_availability_code_raw,
        source_row_number,
        source_file,
        source_url,
        downloaded_at,
        source_sha256,
        loaded_at
    from source

)

select
    -- keys (text on purpose: leading zeros matter)
    snapshot_id,
    nullif(trim(ccn_raw), '')                                            as ccn,

    -- descriptive
    nullif(regexp_replace(trim(facility_name_raw), '\s+', ' ', 'g'), '') as facility_name,
    nullif(regexp_replace(trim(city_raw), '\s+', ' ', 'g'), '')         as city,
    nullif(upper(trim(state_raw)), '')                                   as state,
    nullif(trim(zip_code_raw), '')                                       as zip_code,

    -- services: true / false / NULL (= unknown)
    {{ normalize_yes_no('offers_peritoneal_dialysis_raw') }}             as offers_peritoneal_dialysis,
    {{ normalize_yes_no('offers_home_hemodialysis_training_raw') }}      as offers_home_hemodialysis_training,

    -- star rating: numeric when it parses as a number, otherwise NULL
    case
        when trim(star_rating_raw) ~ '^[0-9]+(\.[0-9]+)?$' then trim(star_rating_raw)::numeric
    end                                                                  as star_rating,
    nullif(trim(star_rating_availability_code_raw), '')                  as star_rating_availability_code,

    -- exact source text, kept for auditing
    offers_peritoneal_dialysis_raw,
    offers_home_hemodialysis_training_raw,
    star_rating_raw,

    -- snapshot metadata
    source_row_number,
    source_file,
    source_url,
    downloaded_at,
    source_sha256,
    loaded_at

from extracted
