-- Grain check: stg_facilities must have exactly one row per snapshot_id + ccn.
-- Returns the offending pairs; the test fails if any rows are returned.
select snapshot_id, ccn, count(*) as row_count
from {{ ref('stg_facilities') }}
group by snapshot_id, ccn
having count(*) > 1
