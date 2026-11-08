# Models (roadmap phase 8)

Trains, tunes, evaluates and explains the models for the two tasks built in `features/`.

    pip install -r requirements.txt
    python train_models.py                       # about 3 minutes; writes ../reports and models/saved/

Optional generalisation test on a dataset from a different simulator seed:

    cd ../simulator && python run.py --reset --seed 7           # load another dataset into the database
    cd ../features  && python build_features.py --out ../data/features_seed7
    cd ../simulator && python run.py --reset                    # put the default seed 42 data back
    cd ../models    && python train_models.py --cross ../data/features_seed7

## What it does
1. Reads the chronological splits from `../data/features`. Tunes three model families on the **validation period only**:
   logistic regression, random forest, gradient boosting.
2. Refits the best settings on train+valid and scores the **test period once**.
3. Shows every model next to two baselines: the prevalence, and the best single feature.
4. 95% confidence intervals by a cluster bootstrap over tenants (M1) or leases (M2).
5. Calibration, precision and lift in the top 25% (M1) or top 10% (M2) of risk scores.
6. Ablation: remove each feature group and refit, and use each group alone.
7. Permutation importance by feature and by group.
8. Diagnostics with the simulator's hidden traits (never used for training): how well predictions track the hidden
   trait, and the AUC an oracle that knew the trait would reach.
9. Optional `--cross`: score the same models on a dataset from another simulator seed.

## Outputs
- `../reports/results.md`: the full results, ready to turn into the paper's results section.
- `../reports/figures/*.png`, `../reports/tables/*.csv`, `../reports/model_card_m1.json`, `model_card_m2.json`.
- `saved/*.joblib`: the fitted primary model of each task (git-ignored).

## Rules you must keep when you extend this
- Choose anything (features, hyperparameters, thresholds) on the validation period. Look at the test period once.
- Never split the tables randomly; the chronological split with purging is what keeps the results honest.
- `hidden_traits.csv` and `anomaly_labels.csv` are answers, not inputs.
- Say "synthetic" next to every number you publish.
