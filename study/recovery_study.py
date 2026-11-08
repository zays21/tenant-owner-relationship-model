"""Recovery study: change how strongly hidden traits drive outcomes, regenerate, retrain, and check that
the pipeline's results move the way the known ground truth says they should.

Axes (everything else fixed):
  renewal_scale  multiplies the tenant-renewal effects (satisfaction, hike, lateness, overpricing)   -> M1 signal
  late_scale     multiplies P(late) in rent payments (late_coef)                                      -> M2 signal
Run:  python recovery_study.py            (no database needed; takes a while)
"""
import json, os, subprocess, sys, itertools
from copy import deepcopy
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for p in ("simulator", "features"):
    sys.path.insert(0, os.path.join(ROOT, p))
from config import CONFIG
from generator import Simulator, summarize, COLUMNS
import build_features as bf
import run as simrun

OUT = os.path.join(ROOT, "study", "runs")
RENEWAL_SCALES = [0.0, 0.5, 1.0, 2.0]
LATE_SCALES = [0.25, 0.5, 1.0]
SEEDS = [11, 12]


def make_tables(sim):
    t = {}
    for name in bf.TABLES:
        df = pd.DataFrame(sim.t[name], columns=COLUMNS[name])
        for c in bf.DATES.get(name, []):
            df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
        for c in bf.FLOATS.get(name, []):
            df[c] = df[c].astype(float)
        t[name] = df
    return t


def one_run(rs, ls, seed):
    tag = f"r{rs}_l{ls}_s{seed}"
    d = os.path.join(OUT, tag)
    if os.path.exists(os.path.join(d, "reports", "tables")):
        return tag
    cfg = deepcopy(CONFIG)
    cfg["seed"] = seed
    for k in ("sat", "hike", "late", "overpriced"):
        cfg["renewal"][k] *= rs
    cfg["payment"]["late_coef"] *= ls
    sim = Simulator(cfg); sim.generate()
    stats = summarize(sim)
    snap = simrun.write_snapshot(sim, cfg, stats, os.path.join(d, "snap"))
    res = bf.build(make_tables(sim), np.datetime64("2026-10-31", "ns"))
    fdir = os.path.join(d, "features"); os.makedirs(fdir, exist_ok=True)
    card = {"snapshot": "2026-10-31", "horizon_days": bf.HORIZON_DAYS, "late_days": bf.LATE_DAYS}
    labels = {"m1": "y_non_renewal", "m2": "y_late_over_7d"}
    id_cols = {"m1": ["lease_id", "tenant_id", "flat_id"], "m2": ["payment_id", "lease_id", "due_date"]}
    for task, (df, cuts) in res.items():
        df.to_csv(os.path.join(fdir, f"{task}_features.csv"), index=False)
        meta = id_cols[task] + ["prediction_date", "label_known_date", "split", labels[task]]
        card[task] = {"rows": int(len(df)), "feature_columns": [c for c in df.columns if c not in meta],
                      "positive_rate": round(float(df[labels[task]].mean()), 4),
                      "split_sizes": df["split"].value_counts().to_dict(),
                      "positive_rate_by_split": {k: round(float(v), 4) for k, v in df.groupby("split")[labels[task]].mean().items()},
                      "split_cutoffs": [str(cuts[0].date()), str(cuts[1].date())]}
    json.dump(card, open(os.path.join(fdir, "feature_card.json"), "w"), indent=2, default=str)
    json.dump({"stats": stats}, open(os.path.join(d, "stats.json"), "w"), default=str)
    subprocess.run([sys.executable, os.path.join(ROOT, "models", "train_models.py"), "--features", fdir,
                    "--hidden", snap, "--reports", os.path.join(d, "reports"),
                    "--models-out", os.path.join(d, "saved"), "--n-boot", "200", "--n-boot-ablation", "60",
                    "--tasks", "m1" if (ls == 1.0 and rs != 1.0) else "m1,m2"],
                   check=True, cwd=os.path.join(ROOT, "models"))
    return tag

if __name__ == "__main__":
    grid = sorted({(rs, 1.0) for rs in RENEWAL_SCALES} | {(1.0, ls) for ls in LATE_SCALES})
    for (rs, ls), seed in itertools.product(grid, SEEDS):
        print("run", rs, ls, seed, flush=True)
        one_run(rs, ls, seed)
