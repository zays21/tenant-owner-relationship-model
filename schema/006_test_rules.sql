-- Each statement below SHOULD FAIL. Run it and check you see all 4 errors.
-- 1) Audit log cannot be edited:
UPDATE audit_log SET actor = 'someone_else' WHERE log_id = 1;
-- 2) Resolution cannot be before creation:
UPDATE complaint SET resolved_at = '2000-01-01' WHERE complaint_id = 1;
-- 3) Same rent month cannot be billed twice for one lease:
INSERT INTO rent_payment (lease_id, due_date, amount_due) VALUES (1, '2025-04-05', 22000);
-- 4) Invalid charge type:
INSERT INTO charge (flat_id, charge_type, amount, billing_month, payer_role, due_date) VALUES (1, 'bribe', 100, '2025-04-01', 'owner', '2025-04-10');
