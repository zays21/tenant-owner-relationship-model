"""Phase 8: train, tune, evaluate and explain the models for the two tasks.

  M1  lease non-renewal (will the tenant NOT renew?)         selection metric: ROC-AUC
  M2  late rent (more than 7 days late, or missing)          selection metric: PR-AUC (classes are imbalanced)

Method (the same for both tasks)
  1. Chronological split from build_features.py. Hyperparameters are chosen on the validation period only.
  2. The chosen model is refit on train+valid (their labels are known before the test period starts) and the
     test period is scored ONCE.
  3. Uncertainty: cluster bootstrap over tenants (or leases), so repeated rows of one tenant do not fake precision.
  4. Always shown next to simple baselines: the prevalence and the best single feature.
  5. Explanation: grouped ablation (drop one feature group / use only one group) and permutation importance.
  6. Diagnostics that need the simulator's hidden traits (never used for training): do the predictions track the
     hidden traits, and what AUC would an oracle that KNOWS the hidden trait reach?

Run:  python train_models.py            (reads ../data/features, writes ../reports and ../models/saved)
"""
import argparse
import glob
import json
import os
import warnings

import joblib
import matplotlib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, precision_recall_curve, roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

warnings.filterwarnings("ignore")

TASKS = {
    "m1": {"label": "y_non_renewal", "title": "Lease non-renewal", "select": "auc", "top_frac": 0.25, "cluster": "tenant_id"},
    "m2": {"label": "y_late_over_7d", "title": "Late rent (more than 7 days)", "select": "ap", "top_frac": 0.10, "cluster": "lease_id"},
}
GROUPS = {
    "lease_terms": ["monthly_rent", "deposit_multiple", "notice_days", "lock_in_months", "is_renewal_lease",
                    "n_prior_renewals", "lease_age_months", "tenancy_age_months", "term_months",
                    "rent_change_vs_previous_lease"],
    "market_rent": ["rent_psf", "market_psf", "rent_to_market"],
    "flat": ["bhk", "area_sqft", "floor_no", "furnishing"],
    "tenant_household": ["tenant_type", "income_band", "occupation_type", "n_household", "n_children", "n_on_agreement"],
    "payment_history": ["n_payments", "late_rate", "mean_late_days", "max_late_days", "n_late_gt7", "n_overdue_now",
                        "last_late_days", "late_streak", "mean_late_last3", "n_partial"],
    "complaints": ["n_complaints", "n_complaints_90d", "n_complaints_30d", "n_open_now", "n_urgent_high", "n_rejected",
                   "mean_days_to_resolve", "max_days_open", "days_since_last_complaint", "share_owner_resp"],
    "society_dues": ["n_tenant_charges", "tenant_charge_late_rate", "n_tenant_penalties"],
    "verification_kyc": ["pv_status", "kyc_docs_verified", "kyc_id_verified"],
    "owner": ["owner_type", "owner_n_past_leases", "owner_past_renewal_rate", "owner_mean_days_to_resolve",
              "owner_n_resp_complaints"],
    "season": ["month_of_year"],
}
GRIDS = {
    "logistic": [{"C": c} for c in (0.01, 0.03, 0.1, 0.3, 1.0)],
    "forest": [{"leaf": l} for l in (5, 20, 50)],
    "boosting": [{"depth": d, "lr": lr, "iters": it, "l2": l2} for d in (2, 3) for lr in (0.03, 0.06)
                 for it in (150, 300) for l2 in (0.0, 5.0)],
}


# ------------------------------------------------------------------------------------------------ data
def design(df, feats, columns=None):
    """Numeric matrix + a map from each original feature to its matrix columns (text features become 0/1 columns)."""
    parts, mapping = [], {}
    for f in feats:
        s = df[f]
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            d = pd.get_dummies(s.fillna("unknown").astype(str), prefix=f, dtype=float)
        else:
            d = s.astype(float).to_frame(f)
        parts.append(d)
        mapping[f] = list(d.columns)
    X = pd.concat(parts, axis=1)
    if columns is not None:
        X = X.reindex(columns=columns, fill_value=0.0)
    return X, mapping


def make_model(kind, hp):
    if kind == "logistic":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=3000, C=hp["C"]))
    if kind == "forest":
        return make_pipeline(SimpleImputer(strategy="median"),
                             RandomForestClassifier(n_estimators=300, min_samples_leaf=hp["leaf"], n_jobs=-1, random_state=0))
    return HistGradientBoostingClassifier(max_depth=hp["depth"], learning_rate=hp["lr"], max_iter=hp["iters"],
                                          l2_regularization=hp["l2"], early_stopping=False, random_state=0)


def score_fn(name):
    return roc_auc_score if name == "auc" else average_precision_score


def metrics(y, p, top_frac):
    k = max(1, int(np.ceil(top_frac * len(y))))
    top = np.argsort(-p, kind="stable")[:k]
    prec = float(np.mean(y[top]))
    return {"roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p)),
            "brier": float(brier_score_loss(y, np.clip(p, 0, 1))), "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))),
            f"precision_top{int(top_frac * 100)}": prec, f"recall_top{int(top_frac * 100)}": float(y[top].sum() / max(1, y.sum())),
            f"lift_top{int(top_frac * 100)}": prec / max(1e-9, float(np.mean(y)))}


def cluster_boot(clusters, n, rng):
    """Resample whole clusters (tenants / leases) with replacement; returns n index arrays."""
    codes, uniq = pd.factorize(clusters)
    members = [np.flatnonzero(codes == i) for i in range(len(uniq))]
    out = []
    for _ in range(n):
        pick = rng.integers(0, len(members), size=len(members))
        out.append(np.concatenate([members[i] for i in pick]).astype(np.int32))
    return out


def boot_scores(y, p, idxs, fn):
    out = []
    for ix in idxs:
        yy = y[ix]
        out.append(fn(yy, p[ix]) if 0 < yy.sum() < len(yy) else np.nan)
    return np.array(out, dtype=float)


def ci(a):
    a = a[~np.isnan(a)]
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def fmt(v, lo=None, hi=None):
    return f"{v:.3f}" if lo is None else f"{v:.3f} [{lo:.3f}, {hi:.3f}]"


# ------------------------------------------------------------------------------------------------ steps
def best_single_feature(Xtr, ytr, Xtest):
    best, sign, name = 0.0, 1, None
    for c in Xtr.columns:
        x = Xtr[c].fillna(Xtr[c].median())
        if x.nunique() < 2:
            continue
        a = roc_auc_score(ytr, x)
        if abs(a - 0.5) > best:
            best, sign, name = abs(a - 0.5), (1 if a > 0.5 else -1), c
    med = Xtr[name].median()
    return name, sign * Xtest[name].fillna(med).values


def tune_and_fit(kind, Xtr, ytr, Xva, yva, Xfit, yfit, sel):
    results = []
    for hp in GRIDS[kind]:
        m = make_model(kind, hp).fit(Xtr, ytr)
        results.append((score_fn(sel)(yva, m.predict_proba(Xva)[:, 1]), hp))
    results.sort(key=lambda r: -r[0])
    best_score, best_hp = results[0]
    final = make_model(kind, best_hp).fit(Xfit, yfit)
    return final, best_hp, best_score


def perm_importance(model, X, y, groups, rng, fn, repeats=5):
    cols = {c: i for i, c in enumerate(X.columns)}
    arr = X.values.copy()
    base = fn(y, model.predict_proba(pd.DataFrame(arr, columns=X.columns))[:, 1])
    out = {}
    for name, members in groups.items():
        idx = [cols[c] for m in members for c in m if c in cols]
        if not idx:
            continue
        drops = []
        for _ in range(repeats):
            Xp = arr.copy()
            Xp[:, idx] = arr[rng.permutation(len(arr))][:, idx]
            drops.append(base - fn(y, model.predict_proba(pd.DataFrame(Xp, columns=X.columns))[:, 1]))
        out[name] = (float(np.mean(drops)), float(np.std(drops)))
    return base, out


def find_snapshot(arg):
    if arg and arg != "auto":
        return arg
    hits = sorted(glob.glob(os.path.join("..", "data", "snapshots", "*", "hidden_traits.csv")), key=os.path.getmtime)
    return os.path.dirname(hits[-1]) if hits else None


def hidden_recovery(task, df, split_mask, p_test, snap):
    """Diagnostics using the simulator's hidden answers. They are never used for training or tuning."""
    if snap is None or not os.path.exists(os.path.join(snap, "hidden_traits.csv")):
        return None
    h = pd.read_csv(os.path.join(snap, "hidden_traits.csv"))
    ten = h[h["entity"] == "tenant"].pivot_table(index="entity_id", columns="trait", values="value")
    test = df[split_mask].copy()
    test["p"] = p_test
    if task == "m2":
        test = test.merge(pd.read_csv(os.path.join(snap, "lease.csv"))[["lease_id", "tenant_id"]], on="lease_id", how="left")
    test = test.join(ten, on="tenant_id")
    y = test[TASKS[task]["label"]].values
    out = {}
    if task == "m2":
        out["spearman_prediction_vs_hidden_reliability"] = float(spearmanr(test["p"], test["reliability"]).statistic)
        out["oracle_auc_knowing_hidden_reliability"] = float(roc_auc_score(y, -test["reliability"]))
    else:
        out["spearman_prediction_vs_hidden_final_satisfaction"] = float(spearmanr(test["p"], test["satisfaction_final"]).statistic)
        out["oracle_auc_knowing_hidden_final_satisfaction"] = float(roc_auc_score(y, -test["satisfaction_final"]))
        out["oracle_auc_knowing_hidden_initial_satisfaction"] = float(roc_auc_score(y, -test["satisfaction"]))
    out["model_auc"] = float(roc_auc_score(y, test["p"]))
    return out


# ------------------------------------------------------------------------------------------------ one task
def run_task(task, args, snap, fig_dir, tab_dir):
    cfg = TASKS[task]
    label, sel, top = cfg["label"], cfg["select"], cfg["top_frac"]
    df = pd.read_csv(os.path.join(args.features, f"{task}_features.csv"), parse_dates=["prediction_date", "label_known_date"])
    card = json.load(open(os.path.join(args.features, "feature_card.json")))[task]
    feats = card["feature_columns"]
    y_all = df[label].values.astype(int)
    tr, va, te = (df["split"] == s for s in ("train", "valid", "test"))
    X, mapping = design(df, feats)
    rng = np.random.default_rng(args.seed)
    print(f"\n=== {task.upper()} {cfg['title']}: train {tr.sum()}, valid {va.sum()}, test {te.sum()}, positive rate {y_all.mean():.3f}")

    # --- models
    fit_mask = tr | va
    fitted, chosen = {}, {}
    for kind in ("logistic", "forest", "boosting"):
        model, hp, vscore = tune_and_fit(kind, X[tr], y_all[tr], X[va], y_all[va], X[fit_mask], y_all[fit_mask], sel)
        fitted[kind], chosen[kind] = model, {"hyperparameters": hp, f"validation_{sel}": round(vscore, 4)}
        print(f"   {kind:9s} chosen {hp}  validation {sel} {vscore:.3f}")
    primary = max(chosen, key=lambda k: chosen[k][f"validation_{sel}"])
    print(f"   primary model (best on validation): {primary}")

    preds = {k: m.predict_proba(X[te])[:, 1] for k, m in fitted.items()}
    prev = float(y_all[fit_mask].mean())
    preds["prevalence"] = np.full(te.sum(), prev)
    sf_name, preds["best_single_feature"] = best_single_feature(X[tr], y_all[tr], X[te])
    yt = y_all[te.values]
    clusters = df.loc[te, cfg["cluster"]].values
    idxs = cluster_boot(clusters, args.n_boot, rng)

    rows = []
    for name, p in preds.items():
        m = metrics(yt, p, top) if name != "prevalence" else {"roc_auc": 0.5, "pr_auc": prev, "brier": float(np.mean((prev - yt) ** 2)),
                                                              "log_loss": float(log_loss(yt, np.full(len(yt), prev)))}
        if name == "best_single_feature":
            m = {k: (v if k not in ("brier", "log_loss") else np.nan) for k, v in m.items()}   # a raw feature is not a probability
        r = {"model": name if name != "best_single_feature" else f"best_single_feature ({sf_name})", **m}
        if name != "prevalence":
            r["roc_auc_ci"] = ci(boot_scores(yt, p, idxs, roc_auc_score))
            r["pr_auc_ci"] = ci(boot_scores(yt, p, idxs, average_precision_score))
        rows.append(r)
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(tab_dir, f"{task}_test_results.csv"), index=False)

    # paired comparison: is the primary model really better than the best single feature?
    d_auc = boot_scores(yt, preds[primary], idxs, roc_auc_score) - boot_scores(yt, preds["best_single_feature"], idxs, roc_auc_score)
    d_ap = boot_scores(yt, preds[primary], idxs, average_precision_score) - boot_scores(yt, preds["best_single_feature"], idxs, average_precision_score)
    paired = {"roc_auc_gain_vs_best_single_feature": (float(np.nanmean(d_auc)),) + ci(d_auc),
              "pr_auc_gain_vs_best_single_feature": (float(np.nanmean(d_ap)),) + ci(d_ap)}

    # --- calibration of the primary model
    pp = preds[primary]
    bins = pd.qcut(pp, 10, duplicates="drop")
    cal = pd.DataFrame({"p": pp, "y": yt, "bin": bins}).groupby("bin", observed=True).agg(mean_pred=("p", "mean"), observed=("y", "mean"), n=("y", "size")).reset_index(drop=True)
    ece = float((cal["n"] * (cal["mean_pred"] - cal["observed"]).abs()).sum() / cal["n"].sum())
    cal.to_csv(os.path.join(tab_dir, f"{task}_calibration.csv"), index=False)

    # --- ablation (same hyperparameters, refit without or with only one group)
    hp = chosen[primary]["hyperparameters"]
    boot_idx_small = idxs[: args.n_boot_ablation]
    full_auc = boot_scores(yt, pp, boot_idx_small, roc_auc_score)
    full_ap = boot_scores(yt, pp, boot_idx_small, average_precision_score)
    abl = []
    for g, members in GROUPS.items():
        present = [f for f in members if f in feats]
        if not present:
            continue
        for mode in ("drop", "only"):
            use = [f for f in feats if (f not in present if mode == "drop" else f in present)]
            if not use:
                continue
            Xa, _ = design(df, use)
            m = make_model(primary, hp).fit(Xa[fit_mask], y_all[fit_mask])
            p = m.predict_proba(Xa[te])[:, 1]
            a, b = roc_auc_score(yt, p), average_precision_score(yt, p)
            row = {"group": g, "mode": mode, "n_features": len(present), "roc_auc": a, "pr_auc": b}
            if mode == "drop":
                da, db = boot_scores(yt, p, boot_idx_small, roc_auc_score) - full_auc, boot_scores(yt, p, boot_idx_small, average_precision_score) - full_ap
                row.update(delta_roc_auc=a - roc_auc_score(yt, pp), delta_roc_auc_ci=ci(da),
                           delta_pr_auc=b - average_precision_score(yt, pp), delta_pr_auc_ci=ci(db))
            abl.append(row)
    abl = pd.DataFrame(abl)
    abl.to_csv(os.path.join(tab_dir, f"{task}_ablation.csv"), index=False)

    # --- permutation importance (primary model, test period)
    fn = score_fn(sel)
    grp_cols = {g: [mapping[f] for f in m if f in mapping] for g, m in GROUPS.items()}
    feat_cols = {f: [mapping[f]] for f in feats}
    _, imp_g = perm_importance(fitted[primary], X[te], yt, grp_cols, rng, fn)
    _, imp_f = perm_importance(fitted[primary], X[te], yt, feat_cols, rng, fn)
    imp_f = dict(sorted(imp_f.items(), key=lambda kv: -kv[1][0])[:15])
    pd.DataFrame([{"group": k, "score_drop": v[0], "sd": v[1]} for k, v in sorted(imp_g.items(), key=lambda kv: -kv[1][0])]).to_csv(os.path.join(tab_dir, f"{task}_importance_groups.csv"), index=False)
    pd.DataFrame([{"feature": k, "score_drop": v[0], "sd": v[1]} for k, v in imp_f.items()]).to_csv(os.path.join(tab_dir, f"{task}_importance_features.csv"), index=False)

    # --- hidden-trait diagnostics
    hid = hidden_recovery(task, df, te.values, pp, snap)

    # --- cross-seed generalisation: models trained on this dataset, scored on a dataset from another simulator seed
    cross = None
    if args.cross and os.path.exists(os.path.join(args.cross, f"{task}_features.csv")):
        d2 = pd.read_csv(os.path.join(args.cross, f"{task}_features.csv"))
        X2, _ = design(d2, feats, columns=X.columns)
        ok = d2["split"] != "purged"
        y2 = d2[label].values.astype(int)
        cross = {}
        for name, mask in (("its_test_period", d2["split"] == "test"), ("all_rows", ok)):
            p2 = fitted[primary].predict_proba(X2[mask])[:, 1]
            cross[name] = {"rows": int(mask.sum()), "roc_auc": float(roc_auc_score(y2[mask], p2)),
                           "pr_auc": float(average_precision_score(y2[mask], p2)), "positive_rate": float(y2[mask].mean())}

    # --- figures
    colors = {"logistic": "#2a6f97", "forest": "#2d7f5e", "boosting": "#b5651d", "best_single_feature": "#7a7a7a", "prevalence": "#bbbbbb"}
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for name, p in preds.items():
        if name == "prevalence":
            continue
        fpr, tpr, _ = roc_curve(yt, p)
        ax[0].plot(fpr, tpr, color=colors[name], lw=2.4 if name == primary else 1.4, label=f"{name} ({roc_auc_score(yt, p):.3f})")
        pr, rc, _ = precision_recall_curve(yt, p)
        ax[1].plot(rc, pr, color=colors[name], lw=2.4 if name == primary else 1.4, label=name)
    ax[0].plot([0, 1], [0, 1], ":", color="#999999")
    ax[0].set(xlabel="False positive rate", ylabel="True positive rate", title="ROC, test period")
    ax[1].axhline(yt.mean(), ls=":", color="#999999")
    ax[1].set(xlabel="Recall", ylabel="Precision", title=f"Precision-recall, test period (base rate {yt.mean():.2f})")
    ax[0].legend(fontsize=8, frameon=False)
    for a_ in ax:
        a_.spines[["top", "right"]].set_visible(False)
    fig.suptitle(cfg["title"], x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, f"{task}_roc_pr.png"), dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.8, 4.4))
    ax.plot([0, max(cal["mean_pred"].max(), cal["observed"].max()) * 1.05] * 1, [0, max(cal["mean_pred"].max(), cal["observed"].max()) * 1.05], ":", color="#999999")
    ax.plot(cal["mean_pred"], cal["observed"], "o-", color=colors[primary])
    ax.set(xlabel="Mean predicted probability", ylabel="Observed rate", title=f"Calibration ({primary}), ECE {ece:.3f}")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, f"{task}_calibration.png"), dpi=150)
    plt.close(fig)

    dd_ = abl[abl["mode"] == "drop"].sort_values("delta_roc_auc")
    fig, ax = plt.subplots(figsize=(7, 4.4))
    err = np.array([[r.delta_roc_auc - r.delta_roc_auc_ci[0], r.delta_roc_auc_ci[1] - r.delta_roc_auc] for r in dd_.itertuples()]).T
    ax.barh(dd_["group"], dd_["delta_roc_auc"], xerr=np.abs(err), color="#2a6f97", ecolor="#555555", capsize=3)
    ax.axvline(0, color="#555555", lw=0.8)
    ax.set(xlabel="Change in test ROC-AUC when the group is removed (95% CI)", title=f"Ablation, {cfg['title']}")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, f"{task}_ablation.png"), dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.6))
    names = list(imp_f)[::-1]
    ax.barh(names, [imp_f[n][0] for n in names], xerr=[imp_f[n][1] for n in names], color="#b5651d", ecolor="#555555", capsize=2)
    ax.set(xlabel=f"Drop in test {'ROC-AUC' if sel == 'auc' else 'PR-AUC'} when the feature is shuffled", title=f"Top features, {cfg['title']}")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, f"{task}_importance.png"), dpi=150)
    plt.close(fig)

    # --- model + model card
    os.makedirs(args.models_out, exist_ok=True)
    joblib.dump({"model": fitted[primary], "columns": list(X.columns), "features": feats}, os.path.join(args.models_out, f"{task}_{primary}.joblib"))
    model_card = {"task": cfg["title"], "label": label, "primary_model": primary, "tuning": chosen,
                  "trained_on": "train+valid chronological periods", "evaluated_on": "test period, scored once",
                  "data": {"rows": int(len(df)), "train": int(tr.sum()), "valid": int(va.sum()), "test": int(te.sum()),
                           "positive_rate": round(float(y_all.mean()), 4), "split_cutoffs": card["split_cutoffs"]},
                  "test_metrics": {r["model"]: {k: v for k, v in r.items() if k != "model"} for r in rows},
                  "calibration_ece": round(ece, 4), "paired_gain_vs_best_single_feature": paired,
                  "intended_use": "learning and research on synthetic data; not for decisions about real people",
                  "known_limits": "trained on simulated behaviour; results describe the simulator, not real tenants"}
    json.dump(model_card, open(os.path.join(args.reports, f"model_card_{task}.json"), "w"), indent=2, default=str)
    return {"cfg": cfg, "n": (int(tr.sum()), int(va.sum()), int(te.sum())), "pos": float(y_all.mean()), "test_pos": float(yt.mean()), "chosen": chosen,
            "primary": primary, "res": res, "paired": paired, "ece": ece, "abl": abl, "imp_g": imp_g, "imp_f": imp_f,
            "sf": sf_name, "hidden": hid, "cross": cross, "top": top, "sel": sel}


# ------------------------------------------------------------------------------------------------ report
def write_report(out, R, args):
    L = ["# Results: phase 8 models", "",
         "All numbers come from the **test period only** (the last 20% of prediction dates), scored once, using models",
         "tuned on the validation period and refit on train+valid. Brackets are 95% cluster-bootstrap intervals",
         f"({args.n_boot} resamples of whole tenants or leases). The data is synthetic: these results describe the",
         "simulator, not real tenants.", ""]
    for task, r in R.items():
        cfg, top = r["cfg"], int(r["top"] * 100)
        L += [f"## {task.upper()}: {cfg['title']}", "",
              f"Rows: train {r['n'][0]}, validation {r['n'][1]}, test {r['n'][2]}. Positive rate {r['pos']:.3f} overall, {r['test_pos']:.3f} in the test period. "
              f"Primary model (best on validation): **{r['primary']}**.", "",
              "### Test results", "",
              f"| Model | ROC-AUC [95% CI] | PR-AUC [95% CI] | Brier | Precision in top {top}% | Lift in top {top}% |", "|---|---|---|---|---|---|"]
        for _, x in r["res"].iterrows():
            auc = fmt(x["roc_auc"], *x["roc_auc_ci"]) if isinstance(x.get("roc_auc_ci"), (tuple, list)) else fmt(x["roc_auc"])
            ap = fmt(x["pr_auc"], *x["pr_auc_ci"]) if isinstance(x.get("pr_auc_ci"), (tuple, list)) else fmt(x["pr_auc"])
            pt = x.get(f"precision_top{top}")
            lf = x.get(f"lift_top{top}")
            L.append(f"| {x['model']} | {auc} | {ap} | {'' if pd.isna(x['brier']) else f"{x['brier']:.3f}"} | {'' if pd.isna(pt) else f'{pt:.3f}'} | {'' if pd.isna(lf) else f'{lf:.2f}'} |")
        pa, pp_ = r["paired"]["roc_auc_gain_vs_best_single_feature"], r["paired"]["pr_auc_gain_vs_best_single_feature"]
        L += ["", f"Gain of the primary model over the best single feature (`{r['sf']}`): ROC-AUC {pa[0]:+.3f} [{pa[1]:+.3f}, {pa[2]:+.3f}], "
              f"PR-AUC {pp_[0]:+.3f} [{pp_[1]:+.3f}, {pp_[2]:+.3f}]. "
              + f"For ROC-AUC the interval {'excludes' if (pa[1] > 0 or pa[2] < 0) else 'includes'} zero; for PR-AUC it {'excludes' if (pp_[1] > 0 or pp_[2] < 0) else 'includes'} zero"
              + (" (a gain that is not clearly different from zero)." if not (pa[1] > 0 and pp_[1] > 0) else "."),
              "", f"Calibration (expected calibration error): {r['ece']:.3f}.", "",
              f"![ROC and PR](figures/{task}_roc_pr.png)", "", f"![Calibration](figures/{task}_calibration.png)", "",
              "### Which feature groups matter (ablation)", "",
              "Change in test ROC-AUC when a group is removed (same hyperparameters, refit), and the AUC when only that group is used.", "",
              "| Group | Features | Change if removed [95% CI] | AUC using only this group |", "|---|---|---|---|"]
        a = r["abl"]
        for g in a[a["mode"] == "drop"].sort_values("delta_roc_auc")["group"]:
            d = a[(a["group"] == g) & (a["mode"] == "drop")].iloc[0]
            o = a[(a["group"] == g) & (a["mode"] == "only")]
            L.append(f"| {g} | {int(d['n_features'])} | {d['delta_roc_auc']:+.3f} [{d['delta_roc_auc_ci'][0]:+.3f}, {d['delta_roc_auc_ci'][1]:+.3f}] | "
                     f"{'' if o.empty else f'{o.iloc[0].roc_auc:.3f}'} |")
        L += ["", f"![Ablation](figures/{task}_ablation.png)", "", "### Permutation importance (primary model)", "",
              "| Feature | Drop in test score when shuffled |", "|---|---|"]
        L += [f"| {k} | {v[0]:.4f} (sd {v[1]:.4f}) |" for k, v in list(r["imp_f"].items())[:10]]
        L += ["", f"![Importance](figures/{task}_importance.png)", ""]
        if r["hidden"]:
            L += ["### Do the predictions track the simulator's hidden traits? (diagnostic only)", ""]
            L += [f"- {k.replace('_', ' ')}: {v:.3f}" for k, v in r["hidden"].items()]
            L += ["", "The oracle AUC is what a scorer that knew the hidden trait exactly would reach on the same test rows. "
                  "It shows how much headroom the data leaves.", ""]
        if r["cross"]:
            L += ["### Generalisation to a dataset from a different simulator seed", "",
                  "| Rows used | N | ROC-AUC | PR-AUC | Positive rate |", "|---|---|---|---|---|"]
            L += [f"| {k.replace('_', ' ')} | {v['rows']} | {v['roc_auc']:.3f} | {v['pr_auc']:.3f} | {v['positive_rate']:.3f} |" for k, v in r["cross"].items()]
            L += [""]
        L += ["### Chosen hyperparameters (selected on validation only)", ""]
        L += [f"- {k}: {v['hyperparameters']}, validation {r['sel']} {v['validation_' + r['sel']]}" for k, v in r["chosen"].items()]
        L += [""]
    open(os.path.join(out, "results.md"), "w").write("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="../data/features")
    ap.add_argument("--hidden", default="auto", help="snapshot folder with hidden_traits.csv and lease.csv (default: newest in ../data/snapshots)")
    ap.add_argument("--cross", default=None, help="feature folder built from a DIFFERENT simulator seed")
    ap.add_argument("--reports", default="../reports")
    ap.add_argument("--models-out", default="saved")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--n-boot-ablation", type=int, default=300)
    ap.add_argument("--tasks", default="m1,m2")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    fig_dir, tab_dir = os.path.join(args.reports, "figures"), os.path.join(args.reports, "tables")
    os.makedirs(fig_dir, exist_ok=True)
    os.makedirs(tab_dir, exist_ok=True)
    snap = find_snapshot(args.hidden)
    print("hidden-trait diagnostics from:", snap)
    R = {t: run_task(t, args, snap, fig_dir, tab_dir) for t in args.tasks.split(",")}
    write_report(args.reports, R, args)
    print("\nReport:", os.path.abspath(os.path.join(args.reports, "results.md")))


if __name__ == "__main__":
    main()
