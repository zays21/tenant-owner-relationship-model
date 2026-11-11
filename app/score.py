"""Score leases (M1) or payments (M2) with a saved model.

    python score.py --task m1 --split test --top 15
    python score.py --task m2 --split test --top 15 --out m2_scores.csv

SYNTHETIC DATA ONLY. Scores describe the simulator, not real people.
Rows in the 'train' and 'valid' splits were used to fit the model, so their scores are in-sample.
"""
import argparse, glob, os
import joblib
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ID_COLS = {"m1": ["lease_id", "tenant_id", "flat_id"], "m2": ["payment_id", "lease_id"]}
LABEL = {"m1": "y_non_renewal", "m2": "y_late_over_7d"}


def design(df, feats, columns):
    """Same numeric matrix the training script builds (text columns become 0/1 columns)."""
    parts = []
    for f in feats:
        s = df[f]
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            parts.append(pd.get_dummies(s.fillna("unknown").astype(str), prefix=f, dtype=float))
        else:
            parts.append(s.astype(float).to_frame(f))
    return pd.concat(parts, axis=1).reindex(columns=columns, fill_value=0.0)


def load_model(task, models_dir):
    files = sorted(glob.glob(os.path.join(models_dir, f"{task}_*.joblib")))
    if not files:
        raise FileNotFoundError(f"No saved model for {task} in {models_dir}. Run models/train_models.py first.")
    return joblib.load(files[0]), os.path.basename(files[0])


def score(df, task, models_dir):
    bundle, _ = load_model(task, models_dir)
    X = design(df, bundle["features"], bundle["columns"])
    return bundle["model"].predict_proba(X)[:, 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["m1", "m2"], required=True)
    ap.add_argument("--features", default=os.path.join(HERE, "..", "data", "features"))
    ap.add_argument("--models", default=os.path.join(HERE, "..", "models", "saved"))
    ap.add_argument("--split", default="test", choices=["train", "valid", "test", "all"])
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    df = pd.read_csv(os.path.join(a.features, f"{a.task}_features.csv"))
    df = df[df["split"] != "purged"]
    if a.split != "all":
        df = df[df["split"] == a.split]
    df = df.assign(risk=score(df, a.task, a.models)).sort_values("risk", ascending=False)
    cols = ID_COLS[a.task] + ["prediction_date", "split", "risk", LABEL[a.task]]
    print(f"\nSYNTHETIC DATA. {len(df)} rows scored ({a.split}). Highest risk first; last column is the simulated outcome.\n")
    print(df[cols].head(a.top).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    if a.split in ("train", "valid"):
        print("\nNote: these rows were used to fit the model, so scores are in-sample and optimistic.")
    if a.out:
        df[cols].to_csv(a.out, index=False); print("Saved", a.out)


if __name__ == "__main__":
    main()
