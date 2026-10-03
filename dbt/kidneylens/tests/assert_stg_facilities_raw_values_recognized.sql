-- Every non-blank raw service / rating value must be recognized by stg_facilities.
-- Catches new CMS values (e.g. 'Limited') that would otherwise silently become NULL/unknown.
select snapshot_id, ccn,
       offers_peritoneal_dialysis_raw,
       offers_home_hemodialysis_training_raw,
       star_rating_raw
from {{ ref('stg_facilities') }}
where (trim(offers_peritoneal_dialysis_raw) <> '' and offers_peritoneal_dialysis is null)
   or (trim(offers_home_hemodialysis_training_raw) <> '' and offers_home_hemodialysis_training is null)
   or (trim(star_rating_raw) <> '' and star_rating is null)
