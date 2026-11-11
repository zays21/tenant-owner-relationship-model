# App: scoring script and dashboard (demo only)

SYNTHETIC DATA ONLY. Scores describe the simulator, not real people. Do not use them on real tenants or owners.

## Needs
Run the simulator, features and models steps first, so these exist:
`data/features/`, `data/snapshots/<newest>/`, `models/saved/*.joblib`, `reports/model_card_*.json`, `reports/tables/`.
No database is needed; everything is read from CSV files.

## Run
    pip install -r requirements.txt
    python score.py --task m1 --split test --top 15      # highest-risk leases, with the simulated outcome
    python score.py --task m2 --split test --out m2.csv  # score all test payments to a file
    streamlit run dashboard.py                            # opens http://localhost:8501

## Dashboard
- Overview: test ROC-AUC with interval for both models, and row counts per split.
- Lease risk (M1): ranked leases, then one lease with risk, rank, rent timing, complaints and key features vs typical.
- Payment risk (M2): precision in the top 10% highest-risk payments, and per-lease payment scores.
- How to read this: what the scores mean and what they must not be used for.

Train and validation rows were used to fit the models, so their scores are in-sample. Use the test split for honest numbers.
