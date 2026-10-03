-- dim_facility_current must contain exactly one snapshot.
-- Fails if it is EMPTY (no snapshot passed validation) or mixes several snapshots.
select count(distinct snapshot_id) as snapshot_count
from {{ ref('dim_facility_current') }}
having count(distinct snapshot_id) <> 1
