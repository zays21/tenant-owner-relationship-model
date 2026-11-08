"""Proves the feature tables do not use information from after the prediction date.

TEST 1  Time travel : for a random sample of rows, delete everything that happened after the row's prediction date
        T, recompute the features from that reduced database, and require them to equal the features computed from
        the full database. A feature that quietly peeks at the future changes when the future is removed.
TEST 2  Split purge : no training label may become known after the first prediction date of the next split.
TEST 3  Columns     : no feature column may be an outcome or a future date.
TEST 4  Too good    : no single feature may predict the label almost perfectly.

Run:  python leakage_checks.py          (exit code 1 if anything fails)
"""
import argparse
import sys

import numpy as np
import pandas as pd

import build_features as bf

BANNED = {"valid_to", "outcome", "y_non_renewal", "y_late_over_7d", "label_known_date", "paid_date", "resolved_at",
          "status", "split"}


def truncate_tables(t, T):
    """The database as it looked at the end of day T: later records removed, later facts hidden."""
    T = pd.Timestamp(T)
    Te = T + pd.Timedelta(days=1)
    o = {k: v.copy() for k, v in t.items()}
    ev = o["lease_event"]
    early = set(ev.loc[ev["event_type"] == "early_termination", "lease_id"])
    L = o["lease"]
    L = L[L["recorded_at"] < Te].copy()
    # an early exit that had not happened yet means the contract end date is not visible
    L.loc[L["lease_id"].isin(early) & (L["valid_to"] > T), "valid_to"] = pd.NaT
    o["lease"] = L
    keep = set(L["lease_id"])
    o["lease_event"] = ev[(ev["recorded_at"] < Te) & ev["lease_id"].isin(keep)].copy()

    P = o["rent_payment"]
    P = P[P["lease_id"].isin(keep) & (P["due_date"] <= T)].copy()
    unk = P["paid_date"].notna() & ((P["paid_date"] > T) | (P["recorded_at"] >= Te))
    P.loc[unk, "paid_date"] = pd.NaT
    P.loc[unk, "amount_paid"] = 0.0
    o["rent_payment"] = P

    C = o["complaint"]
    C = C[C["created_at"] < Te].copy()
    C.loc[C["resolved_at"] >= Te, "resolved_at"] = pd.NaT
    o["complaint"] = C
    cu = o["complaint_update"]
    o["complaint_update"] = cu[(cu["updated_at"] < Te) & cu["complaint_id"].isin(set(C["complaint_id"]))].copy()

    ch = o["charge"]
    ch = ch[ch["billing_month"] <= T].copy()
    ch.loc[ch["paid_date"] > T, "paid_date"] = pd.NaT
    o["charge"] = ch

    F = o["family_member"]
    F = F[F["valid_from"] <= T].copy()
    F.loc[F["valid_to"] > T, "valid_to"] = pd.NaT
    o["family_member"] = F

    PV = o["police_verification"]
    PV = PV[PV["lease_id"].isin(keep)].copy()
    m = PV["submitted_on"] > T
    PV.loc[m, ["submitted_on", "completed_on", "expiry"]] = pd.NaT
    m = PV["completed_on"] > T
    PV.loc[m, ["completed_on", "expiry"]] = pd.NaT
    o["police_verification"] = PV

    K = o["kyc_document"].copy()
    m = K["verified_on"] > T
    K.loc[m, ["verified_on", "expiry"]] = pd.NaT
    o["kyc_document"] = K
    return o


def same(a, b):
    if isinstance(a, (float, np.floating)) or isinstance(b, (float, np.floating)):
        return bool(np.isclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-9))
    return a == b


def in_flight(ctx, lid, T):
    """True when, on day T, a payment was due but not yet paid, or a complaint was open: the cases where a leak shows."""
    T = np.datetime64(T, "ns")
    Te = T + bf.DAY
    p = ctx.pay.get(lid)
    if p is not None and ((p["due"] <= T) & ~np.isnat(p["paid"]) & (p["paid"] > T)).any():
        return True
    c = ctx.cmp_person.get(int(ctx.tenant[int(ctx.rows[lid]["tenant_id"])]["person_id"]))
    if c is not None and ((c["created_at"] < Te) & ~np.isnat(c["resolved_ts"]) & (c["resolved_ts"] >= Te)).any():
        return True
    return False


def time_travel(t, tables, n, seed):
    rng = np.random.default_rng(seed)
    full = bf.Context(t)
    problems = []
    for task in ("m1", "m2"):
        df = tables[task]
        risky = np.array([in_flight(full, int(l), p) for l, p in zip(df["lease_id"], df["prediction_date"])])
        idx_risky = np.flatnonzero(risky)
        idx_other = np.flatnonzero(~risky)
        pick = list(rng.choice(idx_risky, size=min(n // 2 + n % 2, len(idx_risky)), replace=False)) if len(idx_risky) else []
        pick += list(rng.choice(idx_other, size=min(n - len(pick), len(idx_other)), replace=False))
        sample = df.iloc[pick]
        feat_cols = list(full.features(int(sample.iloc[0]["lease_id"]), sample.iloc[0]["prediction_date"], task))
        for _, row in sample.iterrows():
            lid, T = int(row["lease_id"]), row["prediction_date"]
            f_small = bf.Context(truncate_tables(t, T)).features(lid, T, task)
            for c in feat_cols:
                if not same(row[c], f_small[c]):
                    problems.append((task, lid, str(T.date()), c, row[c], f_small[c]))
    return problems


def check_purge(tables):
    problems = []
    for task, df in tables.items():
        tr, va, te = (df[df["split"] == s] for s in ("train", "valid", "test"))
        if len(va) and tr["label_known_date"].max() > va["prediction_date"].min():
            problems.append((task, "a training label is known after the first validation prediction date"))
        if len(te) and va["label_known_date"].max() > te["prediction_date"].min():
            problems.append((task, "a validation label is known after the first test prediction date"))
        if len(va) and tr["prediction_date"].max() >= va["prediction_date"].min():
            problems.append((task, "training and validation periods overlap"))
    return problems


def check_columns(card):
    return [(task, c) for task in ("m1", "m2") for c in card[task]["feature_columns"] if c in BANNED]


def check_too_good(tables):
    from sklearn.metrics import roc_auc_score
    problems, report = [], {}
    for task, label in (("m1", "y_non_renewal"), ("m2", "y_late_over_7d")):
        df = tables[task]
        best = ("", 0.5)
        for c in df.columns:
            if c in BANNED or df[c].dtype == object or str(df[c].dtype).startswith("str") or "date" in c or c.endswith("_id"):
                continue
            x = df[c].astype(float)
            if x.nunique() < 2:
                continue
            auc = roc_auc_score(df[label], x.fillna(x.median()))
            if abs(auc - 0.5) > abs(best[1] - 0.5):
                best = (c, auc)
        report[task] = {"strongest_single_feature": best[0], "auc": round(best[1], 3)}
        if abs(best[1] - 0.5) > 0.4:
            problems.append((task, f"{best[0]} alone gives AUC {best[1]:.3f}"))
    return problems, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default=bf.SNAPSHOT_DEFAULT)
    ap.add_argument("--samples", type=int, default=40, help="rows per task for the time-travel test")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    t = bf.load_tables(bf.connect())
    built = bf.build(t, np.datetime64(a.snapshot, "ns"))
    tables = {k: v[0] for k, v in built.items()}
    card = {k: {"feature_columns": [c for c in tables[k].columns
                                    if c not in ("lease_id", "tenant_id", "flat_id", "payment_id", "due_date",
                                                 "prediction_date", "label_known_date", "split", "y_non_renewal",
                                                 "y_late_over_7d")]} for k in tables}
    failures = 0
    print("TEST 1  time travel (features unchanged when the future is deleted) ...")
    p = time_travel(t, tables, a.samples, a.seed)
    print("   PASS" if not p else f"   FAIL: {len(p)} mismatches, e.g. {p[:5]}")
    failures += bool(p)
    print("TEST 2  split purge ...")
    p = check_purge(tables)
    print("   PASS" if not p else f"   FAIL: {p}")
    failures += bool(p)
    print("TEST 3  no outcome or future columns among the features ...")
    p = check_columns(card)
    print("   PASS" if not p else f"   FAIL: {p}")
    failures += bool(p)
    print("TEST 4  no single feature predicts the label almost perfectly ...")
    p, rep = check_too_good(tables)
    print("   PASS" if not p else f"   FAIL: {p}", rep)
    failures += bool(p)
    print("ALL CHECKS PASSED" if not failures else f"{failures} CHECK(S) FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
