# Simulator, stage 1

Generates synthetic owners, flats, tenants, leases, lease events and monthly rent payments for a housing society
(2018-01-01 to 2026-10-31 by default) and loads them into the `tenant_ert` PostgreSQL database.

## Run it (Windows terminal, from this folder)

    pip install -r requirements.txt
    python run.py --dry-run          # generate only; writes CSV snapshots, no database needed
    python run.py --reset            # wipes the tenant_ert tables, then loads a fresh dataset

`--reset` deletes everything in the tenant_ert tables, including the hand-made rows from 002/005. It asks you to
type `yes`. The password for `ert_user` is asked at run time (or set the ERT_DB_PASSWORD environment variable).
Other options: `--seed 7`, `--flats 2000`, `--out some_folder`, `--no-csv`.

Same seed + same config.py = identical data (checked with file hashes).

## What it writes
- PostgreSQL tables: person, owner, tenant, flat, lease, lease_event, rent_payment
- `data/snapshots/<version>_seed<N>_flats<N>/`: one CSV per table, `data_card.json` (config + summary statistics)
  and `hidden_traits.csv`.

## Hidden traits (important)
Tenant reliability, tenant satisfaction and owner rent-hike habit DRIVE the behaviour but are NOT columns in the
database. They are saved in `hidden_traits.csv` only so you can check later how well a model recovered them.
Never use that file as a model feature.

## Assumptions to document in your paper (all in config.py)
1. Each flat is simulated independently, month by month. Owners and flats are fixed (no resale).
2. A vacant, rentable flat gets a tenant with probability 0.40 per month. Rentable share depends on owner type.
3. Market rent = area x Rs/sq ft x 6% yearly growth x flat quality x furnishing premium x floor premium.
   Replace the starting Rs/sq ft with a value calibrated from a real rent dataset.
4. Lease length 11 months (80%), 12 (12%), 24 (8%). Deposit 1-3 months of rent. Notice 30/60/90 days.
5. Rent is due on the 5th. P(late) = 0.9 x (1 - reliability), higher after a late month. Days late are exponential.
   A small chance of unpaid or partial payment, mostly for low-reliability tenants.
6. At lease end the tenant renews with probability sigmoid(0.9 + 0.8 x satisfaction - 12 x (hike - 5%)
   - 1.5 x late-rate - 3 x overpricing). Otherwise they give notice and move out.
7. After the lock-in, tenants can leave early with a small monthly chance, higher when satisfaction is low.
8. About 20% of records are entered 1-10 days after the real event (recorded_at later than event date).

## Known limits of stage 1 (fix in later stages)
- Each tenant lives in one flat only; tenants never move between flats.
- No complaints, charges, deposit ledger, police verification, KYC or audit history yet. (Stage 2.)
- Satisfaction is fixed per tenant; in stage 2 it should change with complaints and repairs.
- Leases still running at the end date have no outcome yet (right-censored). Exclude them when you build labels.
- No duplicates, typos, missing values or injected anomalies yet. (Stage 2.)
