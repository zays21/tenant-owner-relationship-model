"""Collect the recovery-study runs into one table and one figure."""
import glob, json, os, re
import pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
rows = []
for d in sorted(glob.glob(os.path.join(HERE, "runs", "r*_l*_s*"))):
    m = re.match(r"r([\d.]+)_l([\d.]+)_s(\d+)", os.path.basename(d))
    rs, ls, seed = float(m[1]), float(m[2]), int(m[3])
    T = os.path.join(d, "reports", "tables")
    if not os.path.exists(os.path.join(T, "m1_test_results.csv")):
        continue
    st = json.load(open(os.path.join(d, "stats.json")))["stats"]
    for task in ("m1", "m2"):
        if not os.path.exists(os.path.join(T, f"{task}_test_results.csv")):
            continue
        r = pd.read_csv(os.path.join(T, f"{task}_test_results.csv")).set_index("model")
        a = pd.read_csv(os.path.join(T, f"{task}_ablation.csv"))
        a = a[a["mode"] == "drop"].set_index("group")["delta_roc_auc"]
        rows.append(dict(task=task, renewal_scale=rs, late_scale=ls, seed=seed,
                         auc_boosting=r.loc["boosting", "roc_auc"], auc_best_of_models=r.loc[["logistic", "forest", "boosting"], "roc_auc"].max(),
                         auc_single=r[r.index.str.startswith("best_single")]["roc_auc"].iloc[0],
                         drop_payment_history=a.get("payment_history"), drop_complaints=a.get("complaints"),
                         drop_lease_terms=a.get("lease_terms"),
                         renewal_rate=st.get("renewal_rate_at_natural_end")))
df = pd.DataFrame(rows)
df.to_csv(os.path.join(HERE, "recovery_runs.csv"), index=False)
m1 = df[(df.task == "m1") & (df.late_scale == 1.0)].groupby("renewal_scale").mean(numeric_only=True)
m2 = df[(df.task == "m2") & (df.renewal_scale == 1.0)].groupby("late_scale").mean(numeric_only=True)
sd1 = df[(df.task == "m1") & (df.late_scale == 1.0)].groupby("renewal_scale")["auc_boosting"].std()
sd2 = df[(df.task == "m2") & (df.renewal_scale == 1.0)].groupby("late_scale")["auc_boosting"].std()
print(m1[["auc_boosting", "auc_best_of_models", "auc_single", "drop_payment_history", "drop_complaints", "drop_lease_terms", "renewal_rate"]].round(3))
print(m2[["auc_boosting", "auc_best_of_models", "auc_single", "drop_payment_history", "drop_complaints"]].round(3))
fig, ax = plt.subplots(1, 2, figsize=(10, 4))
for a, g, s, xl, ttl in ((ax[0], m1, sd1, "Strength of renewal effects (1 = default)", "M1 lease non-renewal"),
                         (ax[1], m2, sd2, "Strength of late-payment effect (1 = default)", "M2 late rent")):
    a.errorbar(g.index, g["auc_boosting"], yerr=s.fillna(0), marker="o", capsize=3, color="#2a6f97", label="boosting model")
    a.plot(g.index, g["auc_single"], marker="s", ls="--", color="#777", label="best single feature")
    a.axhline(0.5, color="#999", lw=1, ls=":")
    a.set_xlabel(xl); a.set_ylabel("test ROC-AUC"); a.set_title(ttl)
    a.spines[["top", "right"]].set_visible(False); a.legend(frameon=False)
plt.tight_layout(); plt.savefig(os.path.join(HERE, "recovery_curve.png"), dpi=200)
