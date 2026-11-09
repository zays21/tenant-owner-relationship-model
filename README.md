# tenant-owner-relationship-model
# Tenant–Owner ERT: a bitemporal housing-society database with leakage-safe prediction

A personal learning and research project. It models the relationship between flat owners, tenants and the society
as an **Entity–Relationship–Time (ERT)** database in PostgreSQL, generates realistic linked synthetic data
(no real person's data is used), and builds leakage-safe machine-learning pipelines on top of it for lease
non-renewal and late rent.

> Read this first.* All data is simulated, so every result describes the simulator, not real housing societies.
> The contribution is the design and the evaluation method, not predictive accuracy on real tenants.

---

## What it is used for

| Use | How this project helps |
|---|---|
| *Learning temporal data modelling* | A worked example of valid time vs transaction time, half-open date ranges, exclusion constraints and append-only audit logs. |
| *Learning leakage-safe machine learning | Every feature is computed as of the prediction date, and executable checks prove it (time-travel test, mutation test of the checker). |
| Learning honest evaluation | Chronological splits with purging, cluster bootstrap intervals, single-feature baselines, grouped ablation, calibration, and a recovery study. |
| Research and a review paper | A reproducible testbed with known ground truth, plus a paper draft (`will upload`). |
|A starting point for a real society tool | The schema covers residents, leases, deposits, rent, charges, expenses, complaints and audit trail, and can be filled with real records later (with consent and anonymisation). |
| Testing other methods | Labelled injected anomalies and duplicate people allow anomaly detection and entity-resolution experiments to be scored. |

 Unique selling points

1. Bitemporal by design. Each fact carries when it was true (valid time) and when it was recorded (transaction time), so "what did we believe on date X about date Y?" can be answered.
2. Rules enforced in the database, not in code.** One active lease per flat (PostgreSQL exclusion constraint), generated `late_days`, a balanced deposit ledger and an append-only audit log. We tried to break each rule, and the database rejected it.
3. **Known ground truth.** The simulator keeps hidden traits (tenant reliability and satisfaction, owner behaviour) that real data never exposes. They let us ask whether a model recovers the truth and how much headroom is left.
4. **A leakage checker that is itself tested.** The time-travel check truncates the database to the prediction date, recomputes features and requires an exact match. A deliberately leaky feature is injected to confirm the checker catches it.
5. **A recovery study.** Varying the simulated signal strength shows that model accuracy rises with the signal and falls to chance at zero, which supports that the pipeline measures real signal and does not invent it.
6. **Honest reporting. Intervals, baselines, a parameter-status table (anchored vs assumed) and a limitations list come with every result.
7. Fully reproducible. The same seed and the same `config.py` give byte-identical data.

---

## Repository layout

```
schema/       PostgreSQL schema, test seeds and rule-breaking tests (000-006, fix_ownership.sql)
simulator/    synthetic society generator and database loader
features/     leakage-safe feature tables and leakage checks
models/       model training, evaluation, ablation and reports
study/        recovery study, multi-seed runs, paper builder
docs/         requirements document, anchoring document, paper draft
```

## Data model (summary)

 Core: `person`, `owner`, `tenant`, `flat`, `lease` (valid_from / valid_to as a half-open range; recorded_at / superseded_at).
Resident management:*`family_member`, `emergency_contact`, `kyc_document`, `police_verification`.
- Lease life cycle: `lease_event` (move-in, move-out, renewal, notice), `deposit_ledger`, `rent_payment`.
- Finance: `charge` (maintenance, special, donation, penalty), `expense`, `staff`.
- Complaints: `complaint`, `complaint_update`, `complaint_assignment`.
- **Integrity:** `audit_log` (append-only, written by triggers).

## Prediction tasks

| | M1 lease non-renewal | M2 late rent |
|---|---|---|
| Question | 90 days before a lease ends, will the tenant not renew? | 4 days before rent is due, will it be over 7 days late or unpaid? |
| One row | one lease | one monthly payment |
| Models | logistic regression, random forest, gradient boosting | same |
| Baselines | prevalence, best single feature | same |

M3 (complaint text and priority) and M4 (anomaly detection) are designed but not evaluated.

---

## Quick start (Windows with Git Bash, or Linux/macOS)

Requirements: Python 3.10+, PostgreSQL 16 or newer (tested on 16 and 18).

1. Create the database.In `psql` as the `postgres` user, run `schema/000_setup.sql`. It creates the role `ert_user` and the database `tenant_ert`. Set a password: `ALTER USER ert_user WITH PASSWORD '...';`
2. Create the tables as `ert_user`.** Connect to `tenant_ert` as `ert_user` and run `001`, then `004`. The tables must be owned by `ert_user`. If they are owned by `postgres`, run `fix_ownership.sql` and start again. Files `002` and `005` insert a few sample rows, and `003` and `006` try to break the rules; both are optional, and the loader's `--reset` clears the sample rows.
3. Generate and load data.**
   ```
   cd simulator
   pip install -r requirements.txt
   python run.py --dry-run     # CSV only, no database
   python run.py --reset       # wipes tenant_ert tables, then loads (asks for confirmation)
   ```
4. Build features and run the leakage checks.
   ```
   cd ../features
   pip install -r requirements.txt
   python build_features.py
   python leakage_checks.py    # must print ALL CHECKS PASSED
   ```
5. Train and evaluate models.
   ```
   cd ../models
   pip install -r requirements.txt
   python train_models.py      # about 3 minutes; writes reports/
   ```
6. **Optional studies** (no database needed). From `study/`:
   ```
   python recovery_study.py                                   # about 40 minutes
   python aggregate.py
   python multiseed.py 2000 101,102,103,104,105 m1            # larger M1 study
   python multiseed_summary.py runs_2000
   ```

Set `ERT_DB_PASSWORD` to avoid the password prompt. `--reset` deletes all rows in the `tenant_ert` tables.

## Results at a glance

Test-period ROC-AUC on synthetic data. Full tables, intervals and figures are in `reports/results.md` and the paper draft.

| Task | Model | Best single feature | Note |
|---|---|---|---|
| M1 non-renewal | about 0.60 | about 0.58 | Mean of 5 simulated societies of 2,000 flats; gain +0.026 (range +0.015 to +0.040), consistent across seeds |
| M2 late rent | about 0.71 | about 0.68 | One 500-flat society; payment history drives almost all of it |

Recovery study: M1 model AUC is 0.50 with no signal, then 0.54, 0.58 and 0.63 as the signal grows from 0.5x to 2x of the default.

## Limitations

- **Synthetic data.** Most behavioural parameters (lateness, complaints, vacancy, renewal) are assumptions. Only some are anchored to public sources (see `docs/Simulator_Anchoring.docx`).
- **Weak signal.** M1 AUC around 0.60 is modest, and the single-feature baseline is close.
- **Limited sample.** M2 was evaluated on one seed at 500 flats, and the recovery study used two seeds per setting.
- **Not a production system.** There is no application layer, user interface, access control or privacy review.
- **Real data needs consent.** If real records are ever used, they need written consent, anonymisation and a data-protection review.

## Status

| Phase | State |
|---|---|
| Requirements and design | done |
| Schema and rule tests | done |
| Simulator and loader | done |
| Leakage-safe features and checks | done |
| M1 and M2 models, ablation, calibration | done |
| Recovery study, anchoring, multi-seed M1 | done |
| Paper draft | first draft written; needs author review and reference check |
| Defence Q&A sheet | not started |
| M3 complaint text, M4 anomaly detection | designed, not built |

## Data hygiene

`.gitignore` excludes generated data, features, saved models and secrets. Never commit `.env` or passwords. The synthetic CSVs can be regenerated from a seed, so they do not need to be in the repository.

## Key references

1. P. Chen, "The Entity-Relationship Model: Toward a Unified View of Data," ACM TODS, 1976.
2. R. Snodgrass and I. Ahn, "A Taxonomy of Time in Databases," ACM SIGMOD, 1985.
3. S. Kaufman et al., "Leakage in Data Mining: Formulation, Detection, and Avoidance," ACM TKDD, 2012.
4. Model Tenancy Act, 2021 (Government of India, Ministry of Housing and Urban Affairs).


## Licence

Not chosen yet. Add a `LICENSE` file before making the repository public.
