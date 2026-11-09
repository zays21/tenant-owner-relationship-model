"""Summarise multi-seed M1 runs: mean and spread across seeds."""
import glob, os, sys
import pandas as pd
folder = sys.argv[1] if len(sys.argv) > 1 else "runs_2000"
rows = []
for d in sorted(glob.glob(os.path.join(folder, "*"))):
    T = os.path.join(d, "reports", "tables")
    if not os.path.exists(os.path.join(T, "m1_test_results.csv")):
        continue
    r = pd.read_csv(os.path.join(T, "m1_test_results.csv")).set_index("model")
    sg = [i for i in r.index if i.startswith("best_single")][0]
    a = pd.read_csv(os.path.join(T, "m1_ablation.csv")); a = a[a["mode"] == "drop"].set_index("group")["delta_roc_auc"]
    rows.append(dict(seed=os.path.basename(d), logistic=r.loc["logistic", "roc_auc"], forest=r.loc["forest", "roc_auc"],
                     boosting=r.loc["boosting", "roc_auc"], single=r.loc[sg, "roc_auc"],
                     gain_vs_single=r.loc["boosting", "roc_auc"] - r.loc[sg, "roc_auc"],
                     drop_complaints=a.get("complaints"), drop_payment_history=a.get("payment_history"), drop_lease_terms=a.get("lease_terms")))
df = pd.DataFrame(rows)
df.to_csv(os.path.join(folder, "m1_multiseed.csv"), index=False)
print(df.round(3).to_string(index=False))
print(df.drop(columns="seed").agg(["mean", "std", "min", "max"]).round(3).to_string())
