"""Load a generated dataset into PostgreSQL (tenant_ert) and verify it."""
import getpass
import os

import psycopg2
from psycopg2.extras import execute_values

from generator import COLUMNS

LOAD_ORDER = ["person", "owner", "tenant", "flat", "lease", "lease_event", "rent_payment"]
SERIAL_KEYS = {"person": "person_id", "owner": "owner_id", "tenant": "tenant_id", "flat": "flat_id",
               "lease": "lease_id", "lease_event": "event_id", "rent_payment": "payment_id"}


def connect():
    """Connection settings come from environment variables; the password is never stored in code."""
    password = os.getenv("ERT_DB_PASSWORD") or getpass.getpass("Password for ert_user: ")
    return psycopg2.connect(host=os.getenv("ERT_DB_HOST", "localhost"), port=os.getenv("ERT_DB_PORT", "5432"),
                            dbname=os.getenv("ERT_DB_NAME", "tenant_ert"), user=os.getenv("ERT_DB_USER", "ert_user"),
                            password=password)


def load(conn, tables, reset=False):
    with conn:                                   # one transaction: all or nothing
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
        all_tables = [r[0] for r in cur.fetchall()]
        missing = [t for t in LOAD_ORDER if t not in all_tables]
        if missing:
            raise SystemExit(f"Missing tables {missing}. Run schema/001_entities.sql first.")
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
    return out
