"""Society-wide records created after every flat has been simulated:
maintenance / special / donation / penalty charges, expenses, audit log, duplicate people, anomalies.
"""
import json
from datetime import date, datetime, timedelta

import numpy as np

from generator import add_months
from texts import _typo


def run_postpass(sim):
    idx = {}
    for l in sim.t["lease"]:
        idx.setdefault(l["flat_id"], []).append(l)
    for lst in idx.values():
        lst.sort(key=lambda r: r["valid_from"])
    _charges(sim, idx)
    _expenses(sim)
    _audit(sim)
    _duplicate_people(sim)


# ---------- helpers
def _lease_at(idx, flat_id, d):
    for l in idx.get(flat_id, []):
        if l["valid_from"] <= d < l["valid_to"]:
            return l
    return None


def _occupant(sim, idx, flat, d):
    """Who deals with society dues that follow the occupant: the tenant if the flat is let, else the owner."""
    l = _lease_at(idx, flat["id"], d)
    if l is not None:
        return "tenant", sim.tenants[l["tenant_id"]]["rel"]
    return "owner", sim.owners[flat["owner_id"]]["rel"]


def _pay(sim, due, rel, late_coef, scale_base, scale_slope, p_unpaid):
    r = sim.rng
    if r.random() < p_unpaid:
        return None
    late = 0
    if r.random() < late_coef * (1 - rel):
        late = 1 + int(r.exponential(scale_base + scale_slope * (1 - rel)))
    pd = due + timedelta(days=late)
    return None if pd > sim.end else pd


def _add_charge(sim, flat_id, ctype, amount, billing, role, due, paid, note=None):
    sim.t["charge"].append({"charge_id": sim.nid("charge"), "flat_id": flat_id, "charge_type": ctype,
                            "amount": float(amount), "billing_month": billing, "payer_role": role,
                            "due_date": due, "paid_date": paid, "note": note})


def _round(x, base):
    return max(base, int(round(x / base)) * base)


# ---------- charges
def _charges(sim, idx):
    c, rng = sim.cfg["charges"], sim.rng
    months, m = [], sim.start.replace(day=1)
    while m <= sim.end:
        months.append(m)
        m = add_months(m, 1)

    for flat in sim.flats:
        owner = sim.owners[flat["owner_id"]]
        for M in months:
            due = M.replace(day=c["maintenance_due_day"])
            years = (M - sim.start).days / 365.25
            amount = _round(flat["area"] * c["maintenance_per_sqft"] * (1 + c["maintenance_growth"]) ** years, 50)
            lease = _lease_at(idx, flat["id"], due)
            if lease is not None and sim.lease_payer[lease["lease_id"]] == "tenant":
                role, rel = "tenant", sim.tenants[lease["tenant_id"]]["rel"]
            else:
                role, rel = "owner", owner["rel"]
            p_unpaid = c["p_unpaid_low_rel"] if rel < 0.5 else 0.0
            paid = _pay(sim, due, rel, c["late_coef"], c["late_scale_base"], c["late_scale_slope"], p_unpaid)
            _add_charge(sim, flat["id"], "maintenance", amount, M, role, due, paid)
            if paid is not None and (paid - due).days >= c["penalty_after_days"]:
                pb = add_months(M, 1)
                if pb <= sim.end:
                    pdue = pb.replace(day=c["maintenance_due_day"])
                    ppaid = _pay(sim, pdue, rel, 0.8, 3.0, 10.0, 0.15)
                    _add_charge(sim, flat["id"], "penalty", max(c["penalty_min"], _round(amount * c["penalty_pct"], 50)),
                                pb, role, pdue, ppaid, "late maintenance payment")

    # special one-off charges to every flat, payable by the owner (capital works)
    for year in range(sim.start.year, sim.end.year + 1):
        n_events = sim.pick(c["special_events_per_year"])
        for month in sorted(int(x) for x in rng.choice(np.arange(2, 12), size=n_events, replace=False)):
            billing = date(year, month, 1)
            if billing > sim.end or billing < sim.start:
                continue
            rate = float(rng.uniform(*c["special_per_sqft"]))
            reason = c["special_reasons"][int(rng.integers(len(c["special_reasons"])))]
            due = billing + timedelta(days=30)
            for flat in sim.flats:
                rel = sim.owners[flat["owner_id"]]["rel"]
                paid = _pay(sim, due, rel, 0.7, 3.0, 10.0, 0.02)
                _add_charge(sim, flat["id"], "special", _round(flat["area"] * rate, 100), billing, "owner", due, paid, reason)

    # voluntary festival donation
    for year in range(sim.start.year, sim.end.year + 1):
        billing = date(year, c["donation_month"], 1)
        if billing > sim.end or billing < sim.start:
            continue
        due = billing + timedelta(days=30)
        for flat in sim.flats:
            if not sim.chance(c["donation_prob"]):
                continue
            role, rel = _occupant(sim, idx, flat, billing)
            paid = _pay(sim, due, rel, 0.3, 2.0, 5.0, 0.10)
            _add_charge(sim, flat["id"], "donation", sim.pick(c["donation_amounts"]), billing, role, due, paid, "festival fund")

    # penalties for noise / parking rule violations (offending flat chosen at random)
    for d in sim.violations:
        flat = sim.flats[int(rng.integers(len(sim.flats)))]
        role, rel = _occupant(sim, idx, flat, d)
        due = d + timedelta(days=15)
        if due > sim.end:
            continue
        paid = _pay(sim, due, rel, 0.6, 3.0, 8.0, 0.10)
        _add_charge(sim, flat["id"], "penalty", sim.pick(c["violation_amounts"]), d.replace(day=1), role, due, paid,
                    "rule violation (noise/parking)")
    sim.t["charge"].sort(key=lambda r: (r["billing_month"], r["charge_id"]))
    for i, r in enumerate(sim.t["charge"], 1):          # renumber so ids follow billing order
        r["charge_id"] = i
    sim._ids["charge"] = len(sim.t["charge"])


# ---------- expenses
def _approver(sim, amount):
    return "president" if amount > 20000 else ("treasurer" if sim.chance(0.5) else "secretary")


def _expenses(sim):
    a, rng = sim.cfg["anomalies"], sim.rng
    rows = []
    growth = sim.cfg["salary_growth"]
    m = sim.start.replace(day=1)
    vendor = sim.staff_by_role["vendor"][0]["id"]
    while m <= sim.end:
        years = (m - sim.start).days / 365.25
        for role, base in sim.cfg["salary"].items():
            for s in sim.staff_by_role.get(role, []):
                d = m.replace(day=28)
                if d <= sim.end:
                    amt = _round(base * (1 + growth) ** years, 100)
                    rows.append({"category": f"salary_{role}", "amount": amt, "staff_id": s["id"],
                                 "approved_by": "treasurer", "expense_date": d})
        d = m.replace(day=min(25, 25))
        if d <= sim.end:
            amt = _round(len(sim.flats) * 55 * 1.04 ** years * (1.35 if m.month in (4, 5, 6) else 1.0)
                         * float(rng.lognormal(0, 0.06)), 100)
            rows.append({"category": "electricity_common", "amount": amt, "staff_id": None,
                         "approved_by": _approver(sim, amt), "expense_date": d})
        if m.month in (1, 4, 7, 10) and m.replace(day=15) <= sim.end:
            amt = _round(45000 * 1.05 ** years, 500)
            rows.append({"category": "lift_amc", "amount": amt, "staff_id": vendor,
                         "approved_by": _approver(sim, amt), "expense_date": m.replace(day=15)})
        if m.replace(day=20) <= sim.end:
            amt = _round(6000 * 1.05 ** years, 100)
            rows.append({"category": "garden_pest_control", "amount": amt, "staff_id": None,
                         "approved_by": "secretary", "expense_date": m.replace(day=20)})
        m = add_months(m, 1)
    for r in sim.pending_repairs:
        if r["date"] <= sim.end:
            rows.append({"category": r["category"], "amount": r["amount"], "staff_id": r["staff_id"],
                         "approved_by": _approver(sim, r["amount"]), "expense_date": r["date"]})
    rows.sort(key=lambda r: (r["expense_date"], r["category"], r["staff_id"] or 0))
    extra = []
    for r in rows:
        r["expense_id"] = sim.nid("expense")
        u = float(rng.random())
        if u < a["expense_inflated"]:
            r["amount"] = _round(r["amount"] * float(rng.uniform(3, 8)), 100)
            sim.anomalies.append(("expense", r["expense_id"], "inflated_amount", ""))
        elif u < a["expense_inflated"] + a["expense_duplicate"]:
            dup = dict(r)
            dup["expense_id"] = None
            extra.append((dup, r["expense_id"]))
        elif u < a["expense_inflated"] + a["expense_duplicate"] + a["expense_missing_approval"] and r["amount"] >= 10000:
            r["approved_by"] = None
            sim.anomalies.append(("expense", r["expense_id"], "missing_approval", ""))
    for dup, orig in extra:
        dup["expense_id"] = sim.nid("expense")
        sim.anomalies.append(("expense", dup["expense_id"], "duplicate_entry", orig))
        rows.append(dup)
    sim.t["expense"] = rows


# ---------- audit log
def _audit(sim):
    a, rng = sim.cfg["anomalies"], sim.rng
    entries = []                                     # (logged_at, row, suspicious, payment_id)
    for pay in sim.t["rent_payment"]:
        if pay["paid_date"] is None:
            continue
        u = float(rng.random())
        base = pay["recorded_at"] + timedelta(days=int(rng.integers(1, 4)))
        if u < a["suspicious_edit"] and pay["amount_paid"] > 0:
            logged = base.replace(hour=int(rng.integers(1, 5)), minute=int(rng.integers(0, 60)), second=0)
            if logged > sim.cutoff:
                continue
            old = {"amount_paid": pay["amount_paid"], "paid_date": pay["paid_date"]}
            pay["amount_paid"] = round(pay["amount_paid"] * float(rng.uniform(0.4, 0.8)), 2)
            new = {"amount_paid": pay["amount_paid"], "paid_date": pay["paid_date"]}
            entries.append((logged, old, new, "unknown_user", True, pay["payment_id"]))
        elif u < a["suspicious_edit"] + a["payment_correction"]:
            logged = base.replace(hour=int(rng.integers(10, 18)), minute=int(rng.integers(0, 60)), second=0)
            if logged > sim.cutoff:
                continue
            if rng.random() < 0.5:
                old = {"amount_paid": round(pay["amount_due"] * float(rng.uniform(0.5, 0.95)), 2), "paid_date": pay["paid_date"]}
            else:
                old = {"amount_paid": pay["amount_paid"],
                       "paid_date": pay["paid_date"] + timedelta(days=int(rng.choice([-3, -2, -1, 1, 2, 3])))}
            new = {"amount_paid": pay["amount_paid"], "paid_date": pay["paid_date"]}
            actor = ["clerk_1", "clerk_2", "admin"][int(rng.integers(3))]
            entries.append((logged, old, new, actor, False, pay["payment_id"]))
    entries.sort(key=lambda e: (e[0], e[5]))
    dump = lambda o: json.dumps(o, default=str)
    for logged, old, new, actor, suspicious, pid in entries:
        lid = sim.nid("audit")
        sim.t["audit_log"].append({"log_id": lid, "table_name": "rent_payment", "record_id": str(pid),
                                   "action": "UPDATE", "old_value": dump(old), "new_value": dump(new),
                                   "actor": actor, "logged_at": logged})
        if suspicious:
            sim.anomalies.append(("audit_log", lid, "suspicious_edit", pid))


# ---------- duplicate people (entity-resolution noise)
def _duplicate_people(sim):
    p = sim.cfg["noise"]["duplicate_person_prob"]
    persons = {r["person_id"]: r for r in sim.t["person"]}
    for ten in list(sim.t["tenant"]):
        if not sim.chance(p):
            continue
        orig = persons[ten["person_id"]]
        new_id = sim.nid("person")
        row = dict(orig)
        row["person_id"] = new_id
        row["full_name"] = _typo(sim.rng, orig["full_name"]) if sim.chance(0.7) else orig["full_name"].upper()
        sim.t["person"].append(row)
        sim.anomalies.append(("person", new_id, "duplicate_of", orig["person_id"]))
