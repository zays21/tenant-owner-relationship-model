-- This INSERT SHOULD FAIL. Flat 1 already has a lease covering Jan-Feb 2026.
-- Expected: ERROR: conflicting key value violates exclusion constraint "one_active_lease_per_flat"
INSERT INTO lease (flat_id, owner_id, tenant_id, valid_from, valid_to, monthly_rent, deposit_amount) VALUES
 (1, 1, 2, '2026-01-01', '2026-06-01', 20000, 40000);
