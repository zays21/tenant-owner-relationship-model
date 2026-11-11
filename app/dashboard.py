"""Streamlit dashboard for the tenant-owner ERT project.   Run:  streamlit run dashboard.py
SYNTHETIC DATA ONLY. Reads CSV files, so PostgreSQL does not need to be running."""
import glob, json, os
import numpy as np
import pandas as pd
import streamlit as st
from score import score, load_model, LABEL

HERE = os.path.dirname(os.path.abspath(__file__))
st.set_page_config(page_title="Tenant-Owner ERT demo", layout="wide")

# ---------- paths
st.sidebar.header("Data locations")
snaps = sorted(glob.glob(os.path.join(HERE, "..", "data", "snapshots", "*")))
feat_dir = st.sidebar.text_input("Feature folder", os.path.join(HERE, "..", "data", "features"))
snap_dir = st.sidebar.text_input("Snapshot folder", snaps[-1] if snaps else "")
models_dir = st.sidebar.text_input("Saved models", os.path.join(HERE, "..", "models", "saved"))
reports_dir = st.sidebar.text_input("Reports folder", os.path.join(HERE, "..", "reports"))

st.error("SYNTHETIC DATA. Every person, lease and complaint here is simulated. Scores reflect the simulator's rules, "
         "not real behaviour, and must not be used to judge real tenants or owners.")
st.title("Tenant-Owner relationship model: demo")


@st.cache_data(show_spinner=False)
def load_features(path, task):
    df = pd.read_csv(os.path.join(path, f"{task}_features.csv"))
    df = df[df["split"] != "purged"].copy()
    df["risk"] = score(df, task, models_dir)
    df["risk_pct_in_split"] = df.groupby("split")["risk"].rank(pct=True)
    return df


@st.cache_data(show_spinner=False)
def load_snap(path):
    r = lambda n: pd.read_csv(os.path.join(path, n + ".csv"))
    pay = r("rent_payment"); lease = r("lease"); comp = r("complaint")
    pay["days_late"] = (pd.to_datetime(pay["paid_date"]) - pd.to_datetime(pay["due_date"])).dt.days
    return pay, lease, comp


def card(task):
    p = os.path.join(reports_dir, f"model_card_{task}.json")
    return json.load(open(p)) if os.path.exists(p) else None


try:
    m1 = load_features(feat_dir, "m1"); m2 = load_features(feat_dir, "m2")
    pay, lease, comp = load_snap(snap_dir)
except Exception as e:
    st.warning(f"Could not load data: {e}. Check the folders in the sidebar and that you have run the simulator, "
               "features and models steps.")
    st.stop()

tab0, tab1, tab2, tab3 = st.tabs(["Overview", "Lease risk (M1)", "Payment risk (M2)", "How to read this"])

with tab0:
    c1, c2 = st.columns(2)
    for col, task, title in ((c1, "m1", "M1 lease non-renewal"), (c2, "m2", "M2 late rent")):
        mc = card(task)
        with col:
            st.subheader(title)
            if mc:
                prim = mc["primary_model"]; tm = mc["test_metrics"][prim]
                lo, hi = tm["roc_auc_ci"]
                st.metric("Test ROC-AUC (chosen model: " + prim + ")", f"{tm['roc_auc']:.3f}", f"95% CI {lo:.2f} to {hi:.2f}", delta_color="off")
                st.caption(f"{mc['data']['test']} test rows. 0.5 is chance; 1.0 is perfect.")
            df = m1 if task == "m1" else m2
            st.write(df["split"].value_counts().rename("rows").to_frame().T)
    st.info("Test rows are the most recent period and were scored once. Train and validation rows were used to fit the model, "
            "so their scores look better than they should.")

def feature_table(row, df, task, k=8):
    p = os.path.join(reports_dir, "tables", f"{task}_importance_features.csv")
    if not os.path.exists(p):
        return None
    top = pd.read_csv(p).head(k)["feature"].tolist()
    base = df[df["split"] == "train"]
    out = []
    for f in top:
        if f in row.index and pd.api.types.is_numeric_dtype(df[f]):
            out.append({"feature": f, "this case": row[f], "typical (train median)": base[f].median()})
    return pd.DataFrame(out)

with tab1:
    split = st.selectbox("Split", ["test", "valid", "train"], key="s1")
    d = m1[m1["split"] == split].sort_values("risk", ascending=False)
    if split != "test":
        st.warning("In-sample: the model was fitted on these rows.")
    st.caption("Highest predicted non-renewal risk first. 'Actual' is the simulated outcome.")
    show = d[["lease_id", "tenant_id", "flat_id", "prediction_date", "risk", "y_non_renewal"]].rename(columns={"y_non_renewal": "actual (1 = left)"})
    st.dataframe(show.head(50), width="stretch", hide_index=True)
    lid = st.selectbox("Open a lease", d["lease_id"].tolist()[:500])
    row = d[d["lease_id"] == lid].iloc[0]
    a, b, c = st.columns(3)
    a.metric("Predicted non-renewal risk", f"{row['risk']:.0%}")
    b.metric("Rank within split", f"top {100 - row['risk_pct_in_split'] * 100 + 1:.0f}%")
    c.metric("Actual outcome (simulated)", "left" if row["y_non_renewal"] == 1 else "renewed")
    L = lease[lease["lease_id"] == lid].iloc[0]
    st.write(f"Flat {int(L['flat_id'])}, tenant {int(L['tenant_id'])}, lease {L['valid_from']} to {L['valid_to']}, rent {L['monthly_rent']:,.0f}. "
             f"Prediction made on {row['prediction_date']}, 90 days before the end.")
    pp = pay[pay["lease_id"] == lid].sort_values("due_date")
    cc, cd = st.columns(2)
    with cc:
        st.subheader("Rent timing (days late)")
        if len(pp):
            st.bar_chart(pp.set_index("due_date")["days_late"].fillna(0))
    with cd:
        st.subheader("Complaints from this flat during the lease")
        cm = comp[(comp["flat_id"] == L["flat_id"]) & (comp["created_at"] >= L["valid_from"]) & (comp["created_at"] < L["valid_to"])]
        st.dataframe(cm[["created_at", "category", "priority", "status", "description_text"]], hide_index=True, width="stretch")
    ft = feature_table(row, m1, "m1")
    if ft is not None:
        st.subheader("Most influential features: this case vs typical")
        st.dataframe(ft, hide_index=True, width="stretch")
        st.caption("Features ranked by how much shuffling them hurts the model overall. This is not a per-case explanation.")

with tab2:
    split = st.selectbox("Split", ["test", "valid", "train"], key="s2")
    d = m2[m2["split"] == split]
    if split != "test":
        st.warning("In-sample: the model was fitted on these rows.")
    n = max(1, int(len(d) * 0.10))
    top = d.sort_values("risk", ascending=False).head(n)
    st.write(f"Top 10% highest-risk payments ({n} rows): {top['y_late_over_7d'].mean():.0%} were actually late (over 7 days or unpaid), "
             f"against {d['y_late_over_7d'].mean():.0%} for all payments in this split.")
    lid = st.selectbox("Open a lease", sorted(d["lease_id"].unique().tolist())[:1000], key="l2")
    x = d[d["lease_id"] == lid].sort_values("due_date")[["payment_id", "due_date", "risk", "y_late_over_7d"]]
    st.dataframe(x.rename(columns={"y_late_over_7d": "actual late (1 = yes)"}), hide_index=True, width="stretch")
    if len(x):
        st.line_chart(x.set_index("due_date")["risk"])

with tab3:
    st.markdown("""
**What this is.** Two models trained on simulated housing-society records. M1 predicts, 90 days before a lease ends, whether the tenant will *not* renew.
M2 predicts, 4 days before rent is due, whether it will be over 7 days late or unpaid.

**How the score is built.** Every feature uses only information recorded before the prediction date (checked by a time-travel test).

**How good is it.** M1 is weak (test ROC-AUC about 0.60). M2 is moderate (about 0.71). Payment history does most of the work.

**What the score is.** A probability of the simulator's outcome. It is not a verdict on anyone.

**Do not**: use this on real tenants, or to refuse, penalise or evict anyone. Real use would need consented, anonymised data,
retraining, new leakage checks and human review.
""")
