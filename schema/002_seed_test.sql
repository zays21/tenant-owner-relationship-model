-- Hand-made valid test data. Run as ert_user after 001_entities.sql.
INSERT INTO person (full_name, gender, occupation_type) VALUES
 ('Owner One',   'M', 'business'),
 ('Owner Two',   'F', 'salaried'),
 ('Tenant One',  'M', 'salaried'),
 ('Tenant Two',  'F', 'salaried'),
 ('Tenant Three','M', 'student'),
 ('Tenant Four', 'F', 'self-employed');

INSERT INTO owner (person_id, owner_type, join_date) VALUES
 (1, 'absentee', '2020-01-10'),
 (2, 'resident', '2021-06-01');

INSERT INTO tenant (person_id, tenant_type, income_band) VALUES
 (3, 'family',   'medium'),
 (4, 'bachelor', 'medium'),
 (5, 'student',  'low'),
 (6, 'family',   'high');

INSERT INTO flat (tower, floor_no, unit_no, bhk, area_sqft, furnishing) VALUES
 ('A', 2, 'A-201', 2, 1050, 'semi'),
 ('A', 3, 'A-302', 3, 1450, 'furnished'),
 ('B', 1, 'B-104', 1,  650, 'unfurnished');

-- Flat A-201: Tenant One for 11 months, then renewed (new row, parent_lease_id = 1)
INSERT INTO lease (parent_lease_id, flat_id, owner_id, tenant_id, valid_from, valid_to, monthly_rent, deposit_amount, notice_days, lock_in_months) VALUES
 (NULL, 1, 1, 1, '2025-04-01', '2026-03-01', 22000, 66000, 60, 6),
 (1,    1, 1, 1, '2026-03-01', '2027-02-01', 23500, 66000, 60, 6);

-- Flat A-302: Tenant Two, still open-ended
INSERT INTO lease (flat_id, owner_id, tenant_id, valid_from, valid_to, monthly_rent, deposit_amount) VALUES
 (2, 2, 2, '2025-08-01', NULL, 32000, 96000);

-- Flat B-104: two non-overlapping tenants one after another
INSERT INTO lease (flat_id, owner_id, tenant_id, valid_from, valid_to, monthly_rent, deposit_amount) VALUES
 (3, 1, 3, '2024-07-01', '2025-06-01', 11000, 22000),
 (3, 1, 4, '2025-06-01', '2026-05-01', 12000, 24000);

SELECT lease_id, flat_id, tenant_id, valid_from, valid_to, monthly_rent FROM lease ORDER BY flat_id, valid_from;
