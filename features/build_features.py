"""Leakage-safe feature tables for the two learning tasks.

M1  Lease non-renewal : at 90 days before a fixed-term lease ends, will the tenant NOT renew?
M2  Late rent         : 4 days before a rent due date, will the payment be more than 7 days late (or missing)?

THE RULE: every feature for a row is computed only from what was known on that row's prediction date T.
Facts count as known on T if the event date is on or before T AND the record was entered before the end of T.
Things that were still open on T (an unpaid rent, an unresolved complaint) are treated as open, even if the
database now shows they were settled later. leakage_checks.py proves this by deleting everything after T and
recomputing the features.

Run:  python build_features.py            (reads tenant_ert, writes ../data/features/)
"""
import argparse
import getpass
import json
import os

import numpy as np
import pandas as pd

HORIZON_DAYS = 90          # M1 prediction date = scheduled lease end minus this
LATE_DAYS = 7              # M2 label: more than this many days late
LABEL_WAIT_DAYS = 30       # M2: only rows whose due date is at least this old at the snapshot have a reliable label
M2_LEAD_DAYS = 4           # M2 prediction date = due date minus this
SNAPSHOT_DEFAULT = "2026-10-31"
DAY = np.timedelta64(1, "D")

TABLES = ["lease", "tenant", "person", "flat", "owner", "rent_payment", "complaint", "complaint_update",
          "charge", "family_member", "police_verification", "kyc_document", "lease_event"]
DATES = {"lease": ["valid_from", "valid_to", "recorded_at"], "rent_payment": ["due_date", "paid_date", "recorded_at"],
         "complaint": ["created_at", "resolved_at"], "complaint_update": ["updated_at"],
         "charge": ["billing_month", "due_date", "paid_date"], "family_member": ["valid_from", "valid_to"],
         "police_verification": ["submitted_on", "completed_on", "expiry"], "kyc_document": ["verified_on", "expiry"],
         "lease_event": ["event_date", "recorded_at"]}
FLOATS = {"lease": ["monthly_rent", "deposit_amount"], "rent_payment": ["amount_due", "amount_paid"],
          "charge": ["amount"]}


# ----------------------------------------------------------------------------------------------- loading
def connect():
    import psycopg2
    password = os.getenv("ERT_DB_PASSWORD") or getpass.getpass("Password for ert_user: ")
    return psycopg2.connect(host=os.getenv("ERT_DB_HOST", "localhost"), port=os.getenv("ERT_DB_PORT", "5432"),
                            dbname=os.getenv("ERT_DB_NAME", "tenant_ert"), user=os.getenv("ERT_DB_USER", "ert_user"),
                            password=password)


def load_tables(conn):
    out = {}
    for name in TABLES:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {name}")
        df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        for c in DATES.get(name, []):
            df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
        for c in FLOATS.get(name, []):
            df[c] = df[c].astype(float)
        out[name] = df
    return out


def dd(a, b):
    """Days from b to a (numpy datetimes in, floats out)."""
    return (a - b) / DAY


# ----------------------------------------------------------------------------------------------- as-of context
class Context:
    """Indexes the tables once so that features for one (lease, date T) can be computed quickly."""

    def __init__(self, t):
        self.t = t
        flat = t["flat"][["flat_id", "bhk", "area_sqft", "floor_no", "furnishing"]]
        L = t["lease"].merge(flat, on="flat_id", how="left")
        L["rent_psf"] = L["monthly_rent"] / L["area_sqft"]
        self.rows = {int(r["lease_id"]): r for r in L.to_dict("records")}
        self.children = set(L["parent_lease_id"].dropna().astype(int))
        self.parent = {int(k): (None if pd.isna(v) else int(v)) for k, v in zip(L["lease_id"], L["parent_lease_id"])}
        self.tenant = {int(r["tenant_id"]): r for r in t["tenant"].to_dict("records")}
        self.person = {int(r["person_id"]): r for r in t["person"].to_dict("records")}
        self.owner = {int(r["owner_id"]): r for r in t["owner"].to_dict("records")}

        self.pay = {}
        for lid, g in t["rent_payment"].sort_values("due_date").groupby("lease_id"):
            self.pay[int(lid)] = {"due": g["due_date"].values, "paid": g["paid_date"].values,
                                  "rec": g["recorded_at"].values, "adue": g["amount_due"].values,
                                  "apaid": g["amount_paid"].values}
        # market rent per sq ft by BHK, ordered by lease start
        self.mk = {}
        for bhk, g in L.sort_values("valid_from").groupby("bhk"):
            self.mk[int(bhk)] = (g["valid_from"].values, g["rent_psf"].values)
        self.mk_all = (L.sort_values("valid_from")["valid_from"].values, L.sort_values("valid_from")["rent_psf"].values)

        # complaints: when each one became resolved / rejected, taken from the update history
        cu = t["complaint_update"]
        res = cu[cu["status"] == "resolved"].groupby("complaint_id")["updated_at"].min().rename("resolved_ts")
        rej = cu[cu["status"] == "rejected"].groupby("complaint_id")["updated_at"].min().rename("rejected_ts")
        C = t["complaint"].join(res, on="complaint_id").join(rej, on="complaint_id")
        flat_owner = L.groupby("flat_id")["owner_id"].first()
        C["owner_id"] = C["flat_id"].map(flat_owner)
        for c in ("resolved_ts", "rejected_ts"):
            C[c] = C[c].astype("datetime64[ns]")
        cols = ["created_at", "resolved_ts", "rejected_ts", "priority", "responsibility"]
        arr = lambda g, c: g[c].to_numpy(dtype=object) if c in ("priority", "responsibility") else g[c].values
        self.cmp_person = {int(k): {c: arr(g, c) for c in cols} for k, g in C.groupby("raised_by")}
        owner_c = C[C["responsibility"] == "owner"]
        self.cmp_owner = {int(k): {c: arr(g, c) for c in cols} for k, g in owner_c.groupby("owner_id")}

        ch = t["charge"][t["charge"]["payer_role"] == "tenant"]
        self.chg = {int(k): {"due": g["due_date"].values, "paid": g["paid_date"].values,
                             "type": g["charge_type"].to_numpy(dtype=object)} for k, g in ch.groupby("flat_id")}
        self.fam = {int(k): {"from": g["valid_from"].values, "rel": g["relation"].to_numpy(dtype=object),
                             "agr": g["is_on_agreement"].to_numpy(dtype=bool)}
                    for k, g in t["family_member"].groupby("tenant_id")}
        self.pv = {int(r["lease_id"]): r for r in t["police_verification"].to_dict("records")}
        self.kyc = {int(k): {"ver": g["verified_on"].values, "exp": g["expiry"].values,
                             "type": g["doc_type"].to_numpy(dtype=object)}
                    for k, g in t["kyc_document"].groupby("person_id")}
        # owner history: how each of the owner's leases ended
        self.owner_leases = {}
        for oid, g in L.groupby("owner_id"):
            renewed = g["lease_id"].isin(self.children).values
            self.owner_leases[int(oid)] = (g["valid_to"].values, renewed)

    # ------------------------------------------------------------------------------------------ pieces
    def market_psf(self, bhk, T):
        for lookback in (180, 365, 3650):
            vf, psf = self.mk.get(int(bhk), self.mk_all)
            i0 = np.searchsorted(vf, T - np.timedelta64(lookback, "D"))
            i1 = np.searchsorted(vf, T, side="right")
            if i1 - i0 >= 5:
                return float(np.median(psf[i0:i1]))
        return np.nan

    def chain(self, lid):
        depth, root = 0, lid
        while self.parent.get(root) is not None:
            root, depth = self.parent[root], depth + 1
        return root, depth

    def payment_features(self, lid, T):
        Te = T + DAY
        p = self.pay.get(lid)
        out = dict(n_payments=0, late_rate=np.nan, mean_late_days=np.nan, max_late_days=np.nan, n_late_gt7=0,
                   n_overdue_now=0, last_late_days=np.nan, late_streak=0, mean_late_last3=np.nan, n_partial=0)
        if p is None:
            return out
        m = p["due"] <= T
        if not m.any():
            return out
        due, paid, rec = p["due"][m], p["paid"][m], p["rec"][m]
        known = ~np.isnat(paid) & (paid <= T) & (rec < Te)          # only payments already made and recorded
        late = np.maximum(np.where(known, dd(paid, due), dd(T, due)), 0.0)   # unpaid so far: days overdue on T
        is_late = late > 0
        streak = 0
        for v in is_late[::-1]:
            if not v:
                break
            streak += 1
        out.update(n_payments=int(len(due)), late_rate=float(is_late.mean()), mean_late_days=float(late.mean()),
                   max_late_days=float(late.max()), n_late_gt7=int((late > LATE_DAYS).sum()),
                   n_overdue_now=int((~known & is_late).sum()), last_late_days=float(late[-1]), late_streak=streak,
                   mean_late_last3=float(late[-3:].mean()),
                   n_partial=int((known & (p["apaid"][m] < p["adue"][m] * 0.999)).sum()))
        return out

    def complaint_features(self, person_id, T):
        Te = T + DAY
        out = dict(n_complaints=0, n_complaints_90d=0, n_complaints_30d=0, n_open_now=0, n_urgent_high=0,
                   n_rejected=0, mean_days_to_resolve=np.nan, max_days_open=np.nan, days_since_last_complaint=np.nan,
                   share_owner_resp=np.nan)
        c = self.cmp_person.get(person_id)
        if c is None:
            return out
        m = c["created_at"] < Te
        if not m.any():
            return out
        created = c["created_at"][m]
        resolved = c["resolved_ts"][m]
        rejected = c["rejected_ts"][m]
        res_known = ~np.isnat(resolved) & (resolved < Te)
        rej_known = ~np.isnat(rejected) & (rejected < Te)
        is_open = ~res_known & ~rej_known
        age = dd(Te, created)
        out.update(n_complaints=int(m.sum()), n_complaints_90d=int((age <= 90).sum()),
                   n_complaints_30d=int((age <= 30).sum()), n_open_now=int(is_open.sum()),
                   n_urgent_high=int(np.isin(c["priority"][m], ["urgent", "high"]).sum()),
                   n_rejected=int(rej_known.sum()),
                   days_since_last_complaint=float(age.min()),
                   share_owner_resp=float((c["responsibility"][m] == "owner").mean()))
        if res_known.any():
            out["mean_days_to_resolve"] = float(dd(resolved[res_known], created[res_known]).mean())
        if is_open.any():
            out["max_days_open"] = float(age[is_open].max())
        return out

    def tenant_charge_features(self, flat_id, since, T):
        out = dict(n_tenant_charges=0, tenant_charge_late_rate=np.nan, n_tenant_penalties=0)
        c = self.chg.get(flat_id)
        if c is None:
            return out
        m = (c["due"] <= T) & (c["due"] >= since)
        if not m.any():
            return out
        due, paid = c["due"][m], c["paid"][m]
        known = ~np.isnat(paid) & (paid <= T)
        late = np.maximum(np.where(known, dd(paid, due), dd(T, due)), 0.0)
        out.update(n_tenant_charges=int(m.sum()), tenant_charge_late_rate=float((late > 0).mean()),
                   n_tenant_penalties=int((c["type"][m] == "penalty").sum()))
        return out

    def household_features(self, tenant_id, T):
        out = dict(n_household=0, n_children=0, n_on_agreement=0)
        f = self.fam.get(tenant_id)
        if f is None:
            return out
        m = f["from"] <= T
        return dict(n_household=int(m.sum()), n_children=int((f["rel"][m] == "child").sum()),
                    n_on_agreement=int(f["agr"][m].sum()))

    def verification_features(self, lease_id, person_id, T):
        pv = self.pv.get(lease_id)
        status = "not_submitted"
        if pv is not None and pd.notna(pv["submitted_on"]) and pv["submitted_on"] <= T:
            status = "submitted"
            if pd.notna(pv["completed_on"]) and pv["completed_on"] <= T:
                if pv["status"] == "rejected":
                    status = "rejected"
                elif pd.notna(pv["expiry"]) and pv["expiry"] <= T:
                    status = "expired"
                else:
                    status = "completed"
        k = self.kyc.get(person_id)
        n_verified, id_ok = 0, 0
        if k is not None:
            ok = ~np.isnat(k["ver"]) & (k["ver"] <= T) & (np.isnat(k["exp"]) | (k["exp"] > T))
            n_verified = int(ok.sum())
            id_ok = int((ok & (k["type"] == "id_proof")).any())
        return dict(pv_status=status, kyc_docs_verified=n_verified, kyc_id_verified=id_ok)

    def owner_features(self, owner_id, T):
        out = dict(owner_type=self.owner[owner_id]["owner_type"], owner_n_past_leases=0, owner_past_renewal_rate=np.nan,
                   owner_mean_days_to_resolve=np.nan, owner_n_resp_complaints=0)
        Te = T + DAY
        vt, renewed = self.owner_leases.get(owner_id, (np.array([], dtype="datetime64[ns]"), np.array([], dtype=bool)))
        m = vt <= T                                  # only leases that had ended by T (their outcome was known)
        n = int(m.sum())
        out["owner_n_past_leases"] = n
        out["owner_past_renewal_rate"] = (float(renewed[m].sum()) + 1.0) / (n + 2.0)     # smoothed
        c = self.cmp_owner.get(owner_id)
        if c is not None:
            made = c["created_at"] < Te
            res = c["resolved_ts"]
            known = made & ~np.isnat(res) & (res < Te)
            out["owner_n_resp_complaints"] = int(made.sum())
            if known.any():
                out["owner_mean_days_to_resolve"] = float(dd(res[known], c["created_at"][known]).mean())
        return out

    # ------------------------------------------------------------------------------------------ one row
    def features(self, lid, T, task):
        T = np.datetime64(T, "ns")
        r = self.rows[lid]
        ten = self.tenant[int(r["tenant_id"])]
        person = self.person[int(ten["person_id"])]
        root, depth = self.chain(lid)
        chain_start = self.rows[root]["valid_from"]
        market = self.market_psf(r["bhk"], T)
        f = {
            "monthly_rent": r["monthly_rent"], "deposit_multiple": r["deposit_amount"] / r["monthly_rent"],
            "notice_days": r["notice_days"], "lock_in_months": r["lock_in_months"],
            "is_renewal_lease": int(depth > 0), "n_prior_renewals": depth,
            "lease_age_months": float(dd(T, r["valid_from"]) / 30.4),
            "tenancy_age_months": float(dd(T, chain_start) / 30.4),
            "rent_psf": r["rent_psf"], "market_psf": market, "rent_to_market": r["rent_psf"] / market if market == market else np.nan,
            "rent_change_vs_previous_lease": (r["monthly_rent"] / self.rows[self.parent[lid]]["monthly_rent"] - 1.0)
            if self.parent.get(lid) is not None else np.nan,
            "bhk": r["bhk"], "area_sqft": r["area_sqft"], "floor_no": r["floor_no"],
            "furnishing": r["furnishing"] if isinstance(r["furnishing"], str) else "unknown",
            "tenant_type": ten["tenant_type"],
            "income_band": ten["income_band"] if isinstance(ten["income_band"], str) else "unknown",
            "occupation_type": person["occupation_type"] if isinstance(person["occupation_type"], str) else "unknown",
            "month_of_year": int(T.astype("datetime64[M]").astype(int) % 12 + 1),
        }
        if task == "m1":
            f["term_months"] = float(dd(r["valid_to"], r["valid_from"]) / 30.4)   # contract length: known from the start
        f.update(self.payment_features(lid, T))
        f.update(self.complaint_features(int(ten["person_id"]), T))
        f.update(self.tenant_charge_features(int(r["flat_id"]), chain_start, T))
        f.update(self.household_features(int(r["tenant_id"]), T))
        f.update(self.verification_features(lid, int(ten["person_id"]), T))
        f.update(self.owner_features(int(r["owner_id"]), T))
        return f


# ----------------------------------------------------------------------------------------------- units + labels
def m1_units(t, snapshot):
    """Leases that ran to their natural end before the snapshot. Early terminations and still-running leases are
    left out (their outcome is a different event or not known yet)."""
    L, ev = t["lease"], t["lease_event"]
    early = set(ev.loc[ev["event_type"] == "early_termination", "lease_id"])
    children = set(L["parent_lease_id"].dropna().astype(int))
    u = L[(L["valid_to"] < snapshot) & ~L["lease_id"].isin(early)].copy()
    out = pd.DataFrame({"lease_id": u["lease_id"].values, "tenant_id": u["tenant_id"].values,
                        "flat_id": u["flat_id"].values,
                        "prediction_date": (u["valid_to"] - HORIZON_DAYS * DAY).values,
                        "label_known_date": u["valid_to"].values,
                        "y_non_renewal": (~u["lease_id"].isin(children)).astype(int).values})
    return out


def m2_units(t, snapshot):
    P = t["rent_payment"]
    P = P[P["due_date"] + LABEL_WAIT_DAYS * DAY <= snapshot]
    late = P["paid_date"].isna() | ((P["paid_date"] - P["due_date"]) / DAY > LATE_DAYS)
    known = np.where(late, P["due_date"] + 8 * DAY, P["paid_date"])        # when we would learn the label
    return pd.DataFrame({"payment_id": P["payment_id"].values, "lease_id": P["lease_id"].values,
                         "due_date": P["due_date"].values,
                         "prediction_date": (P["due_date"] - M2_LEAD_DAYS * DAY).values,
                         "label_known_date": pd.to_datetime(known).values,
                         "y_late_over_7d": late.astype(int).values})


def assign_splits(units, train_q=0.6, valid_q=0.8):
    """Chronological split with purging: a training row is kept only if its label was already known on the
    first prediction date of the next split, so no training label depends on the future of the validation set."""
    pdte = units["prediction_date"]
    c1, c2 = pdte.quantile(train_q), pdte.quantile(valid_q)
    split = np.where(pdte >= c2, "test", np.where(pdte >= c1, "valid", "train"))
    split = np.where((split == "train") & (units["label_known_date"] > c1), "purged", split)
    split = np.where((split == "valid") & (units["label_known_date"] > c2), "purged", split)
    units = units.copy()
    units["split"] = split
    return units, (c1, c2)


def build(t, snapshot):
    ctx = Context(t)
    out = {}
    for task, units, idcol in (("m1", m1_units(t, snapshot), "lease_id"), ("m2", m2_units(t, snapshot), "lease_id")):
        units, cuts = assign_splits(units)
        feats = [ctx.features(int(l), p, task) for l, p in zip(units[idcol], units["prediction_date"])]
        df = pd.concat([units.reset_index(drop=True), pd.DataFrame(feats)], axis=1)
        out[task] = (df, cuts)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default=SNAPSHOT_DEFAULT, help="last date of the data (default 2026-10-31)")
    ap.add_argument("--out", default="../data/features")
    a = ap.parse_args()
    t = load_tables(connect())
    res = build(t, np.datetime64(a.snapshot, "ns"))
    os.makedirs(a.out, exist_ok=True)
    card = {"snapshot": a.snapshot, "horizon_days": HORIZON_DAYS, "late_days": LATE_DAYS}
    labels = {"m1": "y_non_renewal", "m2": "y_late_over_7d"}
    id_cols = {"m1": ["lease_id", "tenant_id", "flat_id"], "m2": ["payment_id", "lease_id", "due_date"]}
    for task, (df, cuts) in res.items():
        df.to_csv(os.path.join(a.out, f"{task}_features.csv"), index=False)
        meta = id_cols[task] + ["prediction_date", "label_known_date", "split", labels[task]]
        feature_cols = [c for c in df.columns if c not in meta]
        card[task] = {"rows": int(len(df)), "feature_columns": feature_cols,
                      "positive_rate": round(float(df[labels[task]].mean()), 4),
                      "split_sizes": df["split"].value_counts().to_dict(),
                      "positive_rate_by_split": {k: round(float(v), 4) for k, v in df.groupby("split")[labels[task]].mean().items()},
                      "split_cutoffs": [str(cuts[0].date()), str(cuts[1].date())]}
    with open(os.path.join(a.out, "feature_card.json"), "w") as f:
        json.dump(card, f, indent=2, default=str)
    print(json.dumps({k: v for k, v in card.items() if k in ("m1", "m2")}, indent=2, default=str)[:3000])
    print("Saved to", os.path.abspath(a.out))


if __name__ == "__main__":
    main()
