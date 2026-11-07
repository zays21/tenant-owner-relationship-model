-- Tenant-Owner ERT model, part 2: residents, finance, complaints, audit.
-- Requires 001_entities.sql. Run as ert_user:
--   psql -U ert_user -d tenant_ert -f 004_finance_complaints.sql

-- ===== Resident management =====
CREATE TABLE family_member (
    member_id        SERIAL PRIMARY KEY,
    tenant_id        INT NOT NULL REFERENCES tenant(tenant_id),
    relation         TEXT NOT NULL,
    age_band         TEXT NOT NULL CHECK (age_band IN ('0-12','13-17','18-35','36-60','60+')),
    is_on_agreement  BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from       DATE NOT NULL,
    valid_to         DATE NULL,
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE emergency_contact (
    contact_id   SERIAL PRIMARY KEY,
    person_id    INT NOT NULL REFERENCES person(person_id),
    relation     TEXT NOT NULL,
    phone_hash   TEXT NOT NULL          -- store a hash, never a real number
);

CREATE TABLE kyc_document (
    doc_id             SERIAL PRIMARY KEY,
    person_id          INT NOT NULL REFERENCES person(person_id),
    doc_type           TEXT NOT NULL CHECK (doc_type IN ('id_proof','address_proof','employer_letter','pan','other')),
    doc_number_masked  TEXT,            -- e.g. XXXX-1234, synthetic only
    verified_status    TEXT NOT NULL DEFAULT 'pending' CHECK (verified_status IN ('pending','verified','rejected','expired')),
    verified_on        DATE,
    expiry             DATE
);

CREATE TABLE police_verification (
    pv_id         SERIAL PRIMARY KEY,
    tenant_id     INT NOT NULL REFERENCES tenant(tenant_id),
    lease_id      INT NOT NULL REFERENCES lease(lease_id),
    submitted_on  DATE,
    status        TEXT NOT NULL DEFAULT 'not_submitted' CHECK (status IN ('not_submitted','submitted','completed','expired','rejected')),
    completed_on  DATE,
    expiry        DATE
);

-- ===== Lease lifecycle and money between tenant and owner =====
CREATE TABLE lease_event (
    event_id     SERIAL PRIMARY KEY,
    lease_id     INT NOT NULL REFERENCES lease(lease_id),
    event_type   TEXT NOT NULL CHECK (event_type IN ('move_in','renewal','notice_given','move_out','deposit_returned','early_termination')),
    event_date   DATE NOT NULL,                       -- when it happened in the real world
    recorded_at  TIMESTAMP NOT NULL DEFAULT now(),    -- when the system learned it
    note         TEXT
);

CREATE TABLE rent_payment (
    payment_id   SERIAL PRIMARY KEY,
    lease_id     INT NOT NULL REFERENCES lease(lease_id),
    due_date     DATE NOT NULL,
    paid_date    DATE NULL,
    amount_due   NUMERIC(10,2) NOT NULL CHECK (amount_due > 0),
    amount_paid  NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (amount_paid >= 0),
    mode         TEXT CHECK (mode IN ('upi','bank_transfer','cheque','cash')),
    late_days    INT GENERATED ALWAYS AS (
                    CASE WHEN paid_date IS NULL THEN NULL
                         ELSE GREATEST(paid_date - due_date, 0) END
                 ) STORED,
    recorded_at  TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (lease_id, due_date)
);

CREATE TABLE deposit_ledger (
    entry_id     SERIAL PRIMARY KEY,
    lease_id     INT NOT NULL REFERENCES lease(lease_id),
    entry_type   TEXT NOT NULL CHECK (entry_type IN ('received','deducted','returned')),
    amount       NUMERIC(10,2) NOT NULL CHECK (amount > 0),
    entry_date   DATE NOT NULL,
    reason       TEXT
);

-- ===== Society finance =====
CREATE TABLE charge (
    charge_id      SERIAL PRIMARY KEY,
    flat_id        INT NOT NULL REFERENCES flat(flat_id),
    charge_type    TEXT NOT NULL CHECK (charge_type IN ('maintenance','special','donation','penalty')),
    amount         NUMERIC(10,2) NOT NULL CHECK (amount > 0),
    billing_month  DATE NOT NULL,                      -- first day of the month
    payer_role     TEXT NOT NULL CHECK (payer_role IN ('owner','tenant')),
    due_date       DATE NOT NULL,
    paid_date      DATE NULL,
    note           TEXT
);

CREATE TABLE staff (
    staff_id     SERIAL PRIMARY KEY,
    full_name    TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('plumber','electrician','guard','housekeeping','manager','vendor')),
    active_from  DATE NOT NULL,
    active_to    DATE NULL
);

CREATE TABLE expense (
    expense_id    SERIAL PRIMARY KEY,
    category      TEXT NOT NULL,
    amount        NUMERIC(10,2) NOT NULL CHECK (amount > 0),
    staff_id      INT NULL REFERENCES staff(staff_id),
    approved_by   TEXT,
    expense_date  DATE NOT NULL
);

-- ===== Complaints =====
CREATE TABLE complaint (
    complaint_id       SERIAL PRIMARY KEY,
    flat_id            INT NOT NULL REFERENCES flat(flat_id),
    raised_by          INT NOT NULL REFERENCES person(person_id),
    category           TEXT NOT NULL CHECK (category IN ('plumbing','electrical','noise','parking','lift','security','cleanliness','other')),
    priority           TEXT NOT NULL CHECK (priority IN ('low','medium','high','urgent')),
    description_text   TEXT NOT NULL,
    responsibility     TEXT CHECK (responsibility IN ('owner','tenant','society','unknown')),
    status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','assigned','in_progress','resolved','closed','rejected')),
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    resolved_at        TIMESTAMP NULL,
    CHECK (resolved_at IS NULL OR resolved_at >= created_at)
);

CREATE TABLE complaint_update (
    update_id     SERIAL PRIMARY KEY,
    complaint_id  INT NOT NULL REFERENCES complaint(complaint_id),
    status        TEXT NOT NULL,
    note          TEXT,
    updated_at    TIMESTAMP NOT NULL DEFAULT now(),
    updated_by    TEXT
);

CREATE TABLE complaint_assignment (
    assignment_id  SERIAL PRIMARY KEY,
    complaint_id   INT NOT NULL REFERENCES complaint(complaint_id),
    staff_id       INT NOT NULL REFERENCES staff(staff_id),
    assigned_from  TIMESTAMP NOT NULL,
    assigned_to    TIMESTAMP NULL,
    CHECK (assigned_to IS NULL OR assigned_to >= assigned_from)
);

-- ===== Audit log (append-only) =====
CREATE TABLE audit_log (
    log_id      BIGSERIAL PRIMARY KEY,
    table_name  TEXT NOT NULL,
    record_id   TEXT,
    action      TEXT NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE')),
    old_value   JSONB,
    new_value   JSONB,
    actor       TEXT NOT NULL,
    logged_at   TIMESTAMP NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_row() RETURNS trigger AS $$
-- TG_ARGV[0] = name of the primary key column of the audited table
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO audit_log(table_name, record_id, action, new_value, actor)
        VALUES (TG_TABLE_NAME, to_jsonb(NEW) ->> TG_ARGV[0], 'INSERT', to_jsonb(NEW), current_user);
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO audit_log(table_name, record_id, action, old_value, new_value, actor)
        VALUES (TG_TABLE_NAME, to_jsonb(NEW) ->> TG_ARGV[0], 'UPDATE', to_jsonb(OLD), to_jsonb(NEW), current_user);
    ELSE
        INSERT INTO audit_log(table_name, record_id, action, old_value, actor)
        VALUES (TG_TABLE_NAME, to_jsonb(OLD) ->> TG_ARGV[0], 'DELETE', to_jsonb(OLD), current_user);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION audit_log_readonly() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_change
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_readonly();

CREATE TRIGGER audit_lease        AFTER INSERT OR UPDATE OR DELETE ON lease        FOR EACH ROW EXECUTE FUNCTION audit_row('lease_id');
CREATE TRIGGER audit_rent_payment AFTER INSERT OR UPDATE OR DELETE ON rent_payment FOR EACH ROW EXECUTE FUNCTION audit_row('payment_id');
CREATE TRIGGER audit_charge       AFTER INSERT OR UPDATE OR DELETE ON charge       FOR EACH ROW EXECUTE FUNCTION audit_row('charge_id');
CREATE TRIGGER audit_complaint    AFTER INSERT OR UPDATE OR DELETE ON complaint    FOR EACH ROW EXECUTE FUNCTION audit_row('complaint_id');

CREATE INDEX idx_rent_payment_lease ON rent_payment(lease_id);
CREATE INDEX idx_charge_flat        ON charge(flat_id);
CREATE INDEX idx_complaint_flat     ON complaint(flat_id);
CREATE INDEX idx_audit_table_record ON audit_log(table_name, record_id);
