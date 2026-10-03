-- Enforces docs/metrics.yml for mart_state_services. Returns violating states.
select state, facility_count, training_yes_count, training_no_count,
       training_unknown_count, home_training_share
from {{ ref('mart_state_services') }}
where
    -- every facility is in exactly one bucket
    facility_count <> training_yes_count + training_no_count + training_unknown_count
    -- share is NULL exactly when the denominator is 0
    or (training_yes_count + training_no_count = 0) <> (home_training_share is null)
    -- share is a proportion
    or home_training_share < 0
    or home_training_share > 1
