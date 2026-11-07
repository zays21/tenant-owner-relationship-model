-- Test data for 004. Requires 002_seed_test.sql (uses its lease/flat/person ids).

INSERT INTO family_member (tenant_id, relation, age_band, is_on_agreement, valid_from) VALUES
 (1, 'spouse', '36-60', TRUE,  '2025-04-01'),
 (1, 'child',  '0-12',  FALSE, '2025-04-01');

INSERT INTO emergency_contact (person_id, relation, phone_hash) VALUES
 (3, 'brother', 'hash_a1b2c3'),
 (4, 'mother',  'hash_d4e5f6');

INSERT INTO kyc_document (person_id, doc_type, doc_number_masked, verified_status, verified_on, expiry) VALUES
 (3, 'id_proof',      'XXXX-1111', 'verified', '2025-03-25', '2035-03-25'),
 (4, 'employer_letter', NULL,      'pending',  NULL, NULL);

INSERT INTO police_verification (tenant_id, lease_id, submitted_on, status, completed_on, expiry) VALUES
 (1, 1, '2025-04-05', 'completed', '2025-04-20', '2026-04-20'),
 (2, 3, NULL,         'not_submitted', NULL, NULL);

INSERT INTO lease_event (lease_id, event_type, event_date, recorded_at) VALUES
 (1, 'move_in', '2025-04-01', '2025-04-01 10:00'),
 (2, 'renewal', '2026-03-01', '2026-03-03 12:00');   -- entered 2 days late: valid time vs transaction time

-- Lease 1 rent: on time, 3 days late, 12 days late
INSERT INTO rent_payment (lease_id, due_date, paid_date, amount_due, amount_paid, mode) VALUES
 (1, '2025-04-05', '2025-04-05', 22000, 22000, 'upi'),
 (1, '2025-05-05', '2025-05-08', 22000, 22000, 'upi'),
 (1, '2025-06-05', '2025-06-17', 22000, 22000, 'bank_transfer'),
 (3, '2025-08-05', '2025-08-04', 32000, 32000, 'upi'),
 (3, '2025-09-05', NULL,         32000, 0,     NULL);   -- unpaid

INSERT INTO deposit_ledger (lease_id, entry_type, amount, entry_date, reason) VALUES
 (4, 'received', 22000, '2024-07-01', 'security deposit'),
 (4, 'deducted',  3000, '2025-06-05', 'wall repainting'),
 (4, 'returned', 19000, '2025-06-10', 'balance refunded');

INSERT INTO charge (flat_id, charge_type, amount, billing_month, payer_role, due_date, paid_date, note) VALUES
 (1, 'maintenance', 2500, '2025-04-01', 'owner',  '2025-04-10', '2025-04-09', NULL),
 (1, 'maintenance', 2500, '2025-05-01', 'tenant', '2025-05-10', '2025-05-25', 'paid late'),
 (1, 'penalty',      500, '2025-05-01', 'tenant', '2025-06-10', NULL,         'late maintenance'),
 (2, 'special',     4000, '2025-09-01', 'owner',  '2025-09-20', NULL,         'lift repair fund'),
 (3, 'donation',    1100, '2025-10-01', 'owner',  '2025-10-31', '2025-10-30', 'festival');

INSERT INTO staff (full_name, role, active_from) VALUES
 ('Plumber Ramesh', 'plumber',     '2023-01-01'),
 ('Electrician Sunil', 'electrician', '2023-01-01');

INSERT INTO expense (category, amount, staff_id, approved_by, expense_date) VALUES
 ('plumbing repair', 3500, 1, 'secretary', '2025-06-12'),
 ('guard salary',   18000, NULL, 'treasurer', '2025-06-30');

INSERT INTO complaint (flat_id, raised_by, category, priority, description_text, responsibility, status, created_at, resolved_at) VALUES
 (1, 3, 'plumbing', 'high', 'Water leaking from bathroom ceiling since 2 days', 'owner', 'resolved', '2025-06-10 09:00', '2025-06-12 17:30'),
 (2, 4, 'noise', 'low', 'Loud music from upstairs late at night', 'unknown', 'open', '2025-09-15 23:10', NULL);

INSERT INTO complaint_update (complaint_id, status, note, updated_at, updated_by) VALUES
 (1, 'assigned',    'Assigned to plumber', '2025-06-10 10:00', 'office'),
 (1, 'in_progress', 'Pipe being replaced', '2025-06-11 11:00', 'Plumber Ramesh'),
 (1, 'resolved',    'Fixed and tested',    '2025-06-12 17:30', 'Plumber Ramesh');

INSERT INTO complaint_assignment (complaint_id, staff_id, assigned_from, assigned_to) VALUES
 (1, 1, '2025-06-10 10:00', '2025-06-12 17:30');

-- Audit trail demo: change a rent payment and look at the log
UPDATE rent_payment SET amount_paid = 22000, paid_date = '2025-06-18' WHERE payment_id = 3;

SELECT payment_id, due_date, paid_date, late_days FROM rent_payment ORDER BY payment_id;
SELECT table_name, record_id, action, actor FROM audit_log ORDER BY log_id;
