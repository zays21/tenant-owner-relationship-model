# Feature pipeline (roadmap phase 7)

Builds leakage-safe feature tables for the first two learning tasks from the `tenant_ert` database.

    pip install -r requirements.txt
    python build_features.py          # about 15 seconds; writes ../data/features/
    python leakage_checks.py          # about 1.5 minutes; must print ALL CHECKS PASSED

Run both from this folder. The password for `ert_user` is asked (or set ERT_DB_PASSWORD).
Use `--snapshot YYYY-MM-DD` if you changed `sim_end` in the simulator config (default 2026-10-31).

## The two tasks
| | M1 lease non-renewal | M2 late rent |
|---|---|---|
| Question | 90 days before a fixed-term lease ends, will the tenant NOT renew? | 4 days before a rent is due, will it be paid more than 7 days late (or not at all)? |
| One row | one lease | one monthly rent payment |
| Label | `y_non_renewal` (1 = no renewal) | `y_late_over_7d` (1 = late) |
| Prediction date T | lease end minus 90 days | due date minus 4 days |
| Rows | about 2,700 | about 34,000 |
| Positive rate | about 46% | about 10% |

M1 uses only leases that ran to their natural end before the snapshot. Leases ended early by the tenant (about 6%)
and leases still running are left out, because early exits are a different event and running leases have no outcome yet.
M2 uses payments whose due date is at least 30 days before the snapshot, so the label is settled.

## The rule that prevents leakage
Every feature is computed from what was known on the prediction date T: events dated on or before T that were also
recorded by the end of day T. Anything still open on T (an unpaid rent, an unresolved complaint, a verification still
pending) counts as open, even if the database now shows it was settled later. Features never use the lease
end date of a lease that is still running on T, except the contract length of the M1 lease itself (known from day one).

## Output files (data/features/)
- `m1_features.csv`, `m2_features.csv`: id columns, `prediction_date`, `label_known_date`, `split`, the label, then the features.
- `feature_card.json`: row counts, positive rates, split sizes, split dates, the list of feature columns.

## Splits
`split` is chronological: train (first 60% of prediction dates), valid (next 20%), test (last 20%). A row is marked
`purged` when its label only became known after the next split began. Purged rows are not used for training, so no
training label depends on the future of the validation or test period. Never split these tables randomly.

## Feature groups
Lease terms and rent (rent, deposit multiple, notice, lock-in, renewal depth, rent per sq ft vs the market median of
similar flats that started in the previous 180 days) / flat / tenant and household / rent payment history (late rate,
mean and max days late, streak, overdue now) / complaints raised by the tenant (counts, open now, days to resolve,
urgency, owner-responsibility share) / society dues the tenant pays (late rate, penalties) / police verification and KYC
status on T / owner history (past renewal rate, how fast the owner fixes things, owner type).

## Leakage checks (leakage_checks.py)
1. Time travel: for a sample of rows (half chosen where a payment or complaint was still in flight on T), delete everything
   that happened after T, recompute the features, and require identical values. I checked the checker itself by
   deliberately inserting two leaks; it caught both.
2. Split purge: no training label becomes known after the next split starts.
3. No outcome or future-date column among the features.
4. No single feature predicts the label almost perfectly.

## Honest expectations
Quick sanity run (logistic regression and gradient boosting, train on the first 60%, score the last 20%):
M1 AUC about 0.61, M2 AUC about 0.70 with PR-AUC about 0.21 against a base rate of 0.09. The signal is real but modest,
and the models barely beat one simple feature. That is expected: in the simulator, behaviour is mostly driven by a
few hidden traits plus noise. State this when you write up results; it is a property of the synthetic data.
