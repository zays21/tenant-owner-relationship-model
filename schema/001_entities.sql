-- Tenant-Owner ERT model: core entities, relationships and time.
-- Run as ert_user:  psql -U ert_user -d tenant_ert -f 001_entities.sql

CREATE TABLE person (
    person_id    SERIAL PRIMARY KEY,
    full_name    TEXT NOT NULL,
    gender       TEXT,
    occupation_type TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE owner (
    owner_id     SERIAL PRIMARY KEY,
    person_id    INT NOT NULL REFERENCES person(person_id),
    owner_type   TEXT NOT NULL CHECK (owner_type IN ('resident','absentee','company')),
    join_date    DATE NOT NULL
);

CREATE TABLE tenant (
    tenant_id    SERIAL PRIMARY KEY,
    person_id    INT NOT NULL REFERENCES person(person_id),
    tenant_type  TEXT NOT NULL CHECK (tenant_type IN ('family','bachelor','student','company')),
    income_band  TEXT
);

CREATE TABLE flat (
    flat_id      SERIAL PRIMARY KEY,
    tower        TEXT NOT NULL,
    floor_no     INT  NOT NULL,
    unit_no      TEXT NOT NULL,
    bhk          INT  NOT NULL CHECK (bhk BETWEEN 1 AND 6),
    area_sqft    INT  NOT NULL CHECK (area_sqft > 0),
    furnishing   TEXT CHECK (furnishing IN ('unfurnished','semi','furnished')),
    UNIQUE (tower, unit_no)
);

-- Lease = the central relationship record linking owner, tenant and flat over time.
CREATE TABLE lease (
    lease_id        SERIAL PRIMARY KEY,
    parent_lease_id INT NULL REFERENCES lease(lease_id),   -- set on renewal
    flat_id         INT NOT NULL REFERENCES flat(flat_id),
    owner_id        INT NOT NULL REFERENCES owner(owner_id),
    tenant_id       INT NOT NULL REFERENCES tenant(tenant_id),
    valid_from      DATE NOT NULL,                          -- lease start (real world)
    valid_to        DATE NULL,                              -- lease end, NULL = open
    monthly_rent    NUMERIC(10,2) NOT NULL CHECK (monthly_rent > 0),
    deposit_amount  NUMERIC(10,2) NOT NULL CHECK (deposit_amount >= 0),
    notice_days     INT NOT NULL DEFAULT 30,
    lock_in_months  INT NOT NULL DEFAULT 0,
    recorded_at     TIMESTAMP NOT NULL DEFAULT now(),       -- when the system learned it
    superseded_at   TIMESTAMP NULL,                         -- when this version was replaced
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    -- Only one current (non-superseded) lease per flat at any moment:
    CONSTRAINT one_active_lease_per_flat
        EXCLUDE USING gist (
            flat_id WITH =,
            daterange(valid_from, valid_to) WITH &&
        ) WHERE (superseded_at IS NULL)
);

CREATE INDEX idx_lease_flat   ON lease(flat_id);
CREATE INDEX idx_lease_tenant ON lease(tenant_id);
