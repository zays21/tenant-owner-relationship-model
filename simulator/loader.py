"""Load a generated dataset into PostgreSQL (tenant_ert) and verify it."""
import getpass
import os

import psycopg2
from psycopg2.extras import execute_values

from generator import COLUMNS

LOAD_ORDER = ["person", "owner", "tenant", "flat", "staff", "lease", "lease_event", "family_member",
              "emergency_contact", "kyc_document", "police_verification", "rent_payment", "deposit_ledger",
              "charge", "expense", "complaint", "complaint_update", "complaint_assignment", "audit_log"]
SERIAL_KEYS = {"person": "person_id", "owner": "owner_id", "tenant": "tenant_id", "flat": "flat_id",
               "staff": "staff_id", "lease": "lease_id", "lease_event": "event_id", "family_member": "member_id",
               "emergency_contact": "contact_id", "kyc_document": "doc_id", "police_verification": "pv_id",
               "rent_payment": "payment_id", "deposit_ledger": "entry_id", "charge": "charge_id",
               "expense": "expense_id", "complaint": "complaint_id", "complaint_update": "update_id",
               "complaint_assignment": "assignment_id", "audit_log": "log_id"}


def connect():
    """Connection settings come from environment variables; the password is never stored in code."""
    password = os.getenv("ERT_DB_PASSWORD") or getpass.getpass("Password for ert_user: ")
    return psycopg2.connect(host=os.getenv("ERT_DB_HOST", "localhost"), port=os.getenv("ERT_DB_PORT", "5432"),
                            dbname=os.getenv("ERT_DB_NAME", "tenant_ert"), user=os.getenv("ERT_DB_USER", "ert_user"),
                            password=password)


def load(conn, tables, reset=False):
    with conn:                                   # one transaction: all or nothing
        cur = conn.cursor()
        cur.execute("SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'")
        found = dict(cur.fetchall())
        all_tables = list(found)
        me = conn.get_dsn_parameters()["user"]
        missing = [t for t in LOAD_ORDER if t not in found]
        if missing:
            raise SystemExit(f"Missing tables {missing}. Run schema/001_entities.sql and 004_finance_complaints.sql as ert_user first.")
        wrong_owner = sorted({o for t, o in found.items() if o != me})
        if wrong_owner:
            raise SystemExit(f"The tables are owned by {wrong_owner}, not by {me}. They were probably created while "
                             f"logged in as that user. Drop the public schema and re-create the tables as {me} "
                             f"(see the fix in the chat), then run this again.")
        if reset:
            cur.execute("TRUNCATE " + ", ".join(all_tables) + " RESTART IDENTITY CASCADE")
        else:
            cur.execute("SELECT count(*) FROM lease")
            if cur.fetchone()[0] > 0:
                raise SystemExit("The database already has data. Re-run with --reset to wipe and reload it.")
        # bulk loading would flood audit_log, so pause the audit triggers during the load only
        cur.execute("SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE NOT t.tgisinternal AND t.tgname LIKE 'audit\\_%' AND c.relname <> 'audit_log'")
        audit_triggers = cur.fetchall()
        for rel, trg in audit_triggers:
            cur.execute(f'ALTER TABLE "{rel}" DISABLE TRIGGER "{trg}"')
        for name in LOAD_ORDER:
            cols = COLUMNS[name]
            rows = [tuple(r[c] for c in cols) for r in tables[name]]
            if rows:
                execute_values(cur, f"INSERT INTO {name} ({', '.join(cols)}) VALUES %s", rows, page_size=5000)
        for rel, trg in audit_triggers:
            cur.execute(f'ALTER TABLE "{rel}" ENABLE TRIGGER "{trg}"')
        for name, key in SERIAL_KEYS.items():    # so future inserts do not reuse the generated ids
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{name}', '{key}'), "
                        f"COALESCE((SELECT max({key}) FROM {name}), 1))")


def verify(conn):
    cur = conn.cursor()
    out = {}
    for name in LOAD_ORDER:
        cur.execute(f"SELECT count(*) FROM {name}")
        out[name] = cur.fetchone()[0]
    cur.execute("SELECT min(valid_from), max(valid_from) FROM lease")
    out["lease_start_range"] = cur.fetchone()
    cur.execute("SELECT round(100.0 * avg((late_days > 0)::int), 1) FROM rent_payment WHERE paid_date IS NOT NULL")
    out["pct_paid_late"] = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM lease l JOIN lease_event e ON e.lease_id = l.lease_id "
                "WHERE e.event_type = 'move_in' AND e.event_date < l.valid_from")
    out["move_in_before_lease_start"] = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM lease WHERE valid_to <= valid_from")
    out["bad_date_ranges"] = cur.fetchone()[0]
    # deposit ledger: for every tenancy whose deposit was returned, received = deducted + returned
    cur.execute("SELECT count(*) FROM (SELECT lease_id, sum(CASE WHEN entry_type = 'received' THEN amount ELSE -amount END) AS bal, "
                "bool_or(entry_type = 'returned') AS has_return FROM deposit_ledger GROUP BY lease_id) s "
                "WHERE has_return AND abs(bal) > 0.01")
    out["deposit_ledgers_that_do_not_balance"] = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM complaint WHERE resolved_at < created_at")
    out["complaints_resolved_before_created"] = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM audit_log")
    out["audit_log_rows"] = cur.fetchone()[0]
    return out
