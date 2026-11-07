"""Simulator (stage 2): society, residents, leases, rent, complaints, deposits, verification, KYC.

Design: each flat is simulated independently, month by month, from sim_start to sim_end.
Hidden traits (tenant reliability, tenant satisfaction, owner responsiveness, owner payment reliability,
owner rent-hike habit) DRIVE behaviour but are never written to the database. They go to
hidden_traits.csv for validation only. Society-wide records (charges, expenses, audit log, duplicates,
anomalies) are produced afterwards in postpass.py.
"""
import calendar
import hashlib
import math
from datetime import date, datetime, timedelta

import numpy as np

from texts import make_text

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Rahul", "Amit", "Vikram", "Manish",
               "Ananya", "Diya", "Priya", "Neha", "Pooja", "Kavya", "Riya", "Sneha", "Isha", "Meera"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Mehta", "Jain", "Agarwal", "Chauhan", "Kapoor",
              "Reddy", "Nair", "Das", "Banerjee", "Khan", "Joshi", "Malhotra", "Bansal", "Saxena", "Tiwari"]
ROLE_FOR = {"plumbing": "plumber", "electrical": "electrician", "lift": "vendor", "security": "guard",
            "cleanliness": "housekeeping", "noise": "manager", "parking": "manager", "other": "manager"}

# Column order per table (shared by the DB loader and the CSV export)
COLUMNS = {
    "person": ["person_id", "full_name", "gender", "occupation_type", "created_at"],
    "owner": ["owner_id", "person_id", "owner_type", "join_date"],
    "tenant": ["tenant_id", "person_id", "tenant_type", "income_band"],
    "flat": ["flat_id", "tower", "floor_no", "unit_no", "bhk", "area_sqft", "furnishing"],
    "staff": ["staff_id", "full_name", "role", "active_from", "active_to"],
    "lease": ["lease_id", "parent_lease_id", "flat_id", "owner_id", "tenant_id", "valid_from", "valid_to",
              "monthly_rent", "deposit_amount", "notice_days", "lock_in_months", "recorded_at", "superseded_at"],
    "lease_event": ["event_id", "lease_id", "event_type", "event_date", "recorded_at", "note"],
    "family_member": ["member_id", "tenant_id", "relation", "age_band", "is_on_agreement", "valid_from", "valid_to"],
    "emergency_contact": ["contact_id", "person_id", "relation", "phone_hash"],
    "kyc_document": ["doc_id", "person_id", "doc_type", "doc_number_masked", "verified_status", "verified_on",
                     "expiry"],
    "police_verification": ["pv_id", "tenant_id", "lease_id", "submitted_on", "status", "completed_on", "expiry"],
    "rent_payment": ["payment_id", "lease_id", "due_date", "paid_date", "amount_due", "amount_paid", "mode",
                     "recorded_at"],
    "deposit_ledger": ["entry_id", "lease_id", "entry_type", "amount", "entry_date", "reason"],
    "charge": ["charge_id", "flat_id", "charge_type", "amount", "billing_month", "payer_role", "due_date",
               "paid_date", "note"],
    "expense": ["expense_id", "category", "amount", "staff_id", "approved_by", "expense_date"],
    "complaint": ["complaint_id", "flat_id", "raised_by", "category", "priority", "description_text",
                  "responsibility", "status", "created_at", "resolved_at"],
    "complaint_update": ["update_id", "complaint_id", "status", "note", "updated_at", "updated_by"],
    "complaint_assignment": ["assignment_id", "complaint_id", "staff_id", "assigned_from", "assigned_to"],
    "audit_log": ["log_id", "table_name", "record_id", "action", "old_value", "new_value", "actor", "logged_at"],
}


def add_months(d, n):
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month_start_on_or_after(d):
    return d if d.day == 1 else add_months(d.replace(day=1), 1)


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def round500(x):
    return max(3000, int(round(x / 500.0)) * 500)


def ts(dt):
    return dt.replace(microsecond=0)


class Simulator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg["seed"])
        self.start, self.end = cfg["sim_start"], cfg["sim_end"]
        self.cutoff = datetime(self.end.year, self.end.month, self.end.day, 23, 59, 59)
        self.t = {name: [] for name in COLUMNS}
        self.hidden = []             # (entity, id, trait, value)   ground truth, never a feature
        self.anomalies = []          # (entity, id, type, detail)   labels for anomaly / quality tests
        self.outcome = {}            # lease_id -> renewed | moved_out | early_exit | active  (analysis only)
        self.lease_payer = {}        # lease_id -> who pays maintenance: owner | tenant
        self.pending_repairs = []    # repair expenses created by complaints, finished in postpass
        self.violations = []         # dates of noise/parking rule violations (penalty charges, postpass)
        self.tenants = {}
        self._ids = {}
        self.owners = {}
        self.flats = []
        self.staff_by_role = {}

    # ---------- small helpers
    def nid(self, name):
        self._ids[name] = self._ids.get(name, 0) + 1
        return self._ids[name]

    def pick(self, spec, probs=None):
        if probs is None and isinstance(spec, tuple):
            options, probs = spec
        else:
            options = spec                      # a plain list with no probabilities = uniform choice
        return options[int(self.rng.choice(len(options), p=probs))]

    def chance(self, p):
        return bool(self.rng.random() < p)

    def entered(self, d, hour):
        """Timestamp when a real-world event dated d was recorded; ~20% are entered late."""
        delay = int(self.rng.integers(1, self.cfg["late_entry_max_days"] + 1)) \
            if self.chance(self.cfg["late_entry_prob"]) else 0
        return datetime(d.year, d.month, d.day, hour) + timedelta(days=delay)

    def phone_hash(self):
        return hashlib.sha256(str(int(self.rng.integers(6_000_000_000, 9_999_999_999))).encode()).hexdigest()[:16]

    def person_name(self, suffix=""):
        first = FIRST_NAMES[int(self.rng.integers(len(FIRST_NAMES)))]
        last = LAST_NAMES[int(self.rng.integers(len(LAST_NAMES)))]
        return f"{first} {last}{suffix}"

    def new_person(self, suffix="", occupation="salaried"):
        nz = self.cfg["noise"]
        pid = self.nid("person")
        self.t["person"].append({
            "person_id": pid, "full_name": self.person_name(suffix),
            "gender": None if self.chance(nz["missing_gender"]) else ("M" if self.chance(0.55) else "F"),
            "occupation_type": None if self.chance(nz["missing_occupation"]) else occupation,
            "created_at": datetime(self.start.year, self.start.month, self.start.day)})
        return pid

    def market_rent(self, flat, d):
        c = self.cfg
        years = (d - self.start).days / 365.25
        return (c["rent_per_sqft_start"] * (1 + c["rent_annual_growth"]) ** years * flat["area"]
                * flat["quality"] * c["furnishing_premium"][flat["furn"]]
                * (1 + c["floor_premium_per_floor"] * (flat["floor"] - 1)))

    # ---------- society
    def _make_staff(self):
        for role, n in self.cfg["staff"].items():
            for _ in range(n):
                sid, name = self.nid("staff"), self.person_name()
                self.t["staff"].append({"staff_id": sid, "full_name": name, "role": role,
                                        "active_from": self.start, "active_to": None})
                self.staff_by_role.setdefault(role, []).append({"id": sid, "name": name, "role": role})

    def _make_flats(self):
        c = self.cfg
        per_tower = c["floors"] * c["units_per_floor"]
        n_towers = math.ceil(c["n_flats"] / per_tower)
        for ti in range(n_towers):
            tower = chr(65 + ti) if ti < 26 else f"T{ti + 1}"
            for fl in range(1, c["floors"] + 1):
                for u in range(1, c["units_per_floor"] + 1):
                    if len(self.flats) >= c["n_flats"]:
                        break
                    bhk = self.pick(c["bhk"])
                    area = int(c["area_by_bhk"][bhk] * (1 + self.rng.normal(0, 0.06)))
                    furn = self.pick(c["furnishing"])
                    flat = {"id": self.nid("flat"), "tower": tower, "floor": fl, "unit": f"{tower}-{fl}{u:02d}",
                            "bhk": bhk, "area": area, "furn": furn,
                            "quality": float(self.rng.lognormal(0, 0.07)), "owner_id": None, "rentable": False}
                    self.flats.append(flat)
                    self.t["flat"].append({"flat_id": flat["id"], "tower": tower, "floor_no": fl,
                                           "unit_no": flat["unit"], "bhk": bhk, "area_sqft": area,
                                           "furnishing": None if self.chance(c["noise"]["missing_furnishing"]) else furn})

    def _kyc_doc(self, pid, doc_type, base_date, status=None):
        c = self.cfg
        status = status or self.pick(c["kyc"]["id_status"])
        verified_on = base_date + timedelta(days=int(self.rng.integers(0, 11))) if status in ("verified", "expired") else None
        if verified_on and verified_on > self.end:
            verified_on, status = None, "pending"
        expiry = None
        if verified_on:
            expiry = verified_on + timedelta(days=3650 if doc_type == "id_proof" else 365)
            if status == "expired":
                expiry = min(expiry, verified_on + timedelta(days=int(self.rng.integers(200, 700))))
        number = None if self.chance(c["noise"]["missing_doc_number"]) else f"XXXX-{int(self.rng.integers(1000, 9999))}"
        self.t["kyc_document"].append({"doc_id": self.nid("kyc"), "person_id": pid, "doc_type": doc_type,
                                       "doc_number_masked": number, "verified_status": status,
                                       "verified_on": verified_on, "expiry": expiry})

    def _make_owners(self):
        c = self.cfg
        n_owners = max(1, int(len(self.flats) * c["owner_ratio"]))
        order = self.rng.permutation(len(self.flats))
        for _ in range(n_owners):
            otype = self.pick(c["owner_types"])
            pid = self.new_person(" Holdings" if otype == "company" else "", "business" if otype == "company" else "salaried")
            oid = self.nid("owner")
            hike_mean = float(np.clip(self.rng.normal(*c["owner_hike_mean"]), 0.0, 0.15))
            resp = float(self.rng.beta(4, 2))                       # how quickly the owner fixes things
            rel = float(self.rng.beta(*c["charges"]["owner_reliability_beta"]))   # how reliably the owner pays dues
            self.owners[oid] = {"id": oid, "type": otype, "hike_mean": hike_mean, "resp": resp, "rel": rel}
            join = self.start - timedelta(days=int(self.rng.integers(0, 1500)))
            self.t["owner"].append({"owner_id": oid, "person_id": pid, "owner_type": otype, "join_date": join})
            self._kyc_doc(pid, "id_proof", join, status="verified")
            for trait, val in (("hike_mean", hike_mean), ("responsiveness", resp), ("payment_reliability", rel)):
                self.hidden.append(("owner", oid, trait, round(val, 4)))
        for rank, fi in enumerate(order):
            oid = rank + 1 if rank < n_owners else int(self.rng.integers(1, n_owners + 1))
            flat = self.flats[int(fi)]
            flat["owner_id"] = oid
            flat["rentable"] = self.chance(c["rentable_prob"][self.owners[oid]["type"]])

    def _new_tenant(self, start):
        c, nz = self.cfg, self.cfg["noise"]
        ttype = self.pick(c["tenant_types"])
        occupation = {"family": self.pick(["salaried", "business", "self-employed"], [0.6, 0.25, 0.15]),
                      "bachelor": self.pick(["salaried", "self-employed", "business"], [0.7, 0.2, 0.1]),
                      "student": "student", "company": "corporate_lease"}[ttype]
        income = "high" if ttype == "company" else "low" if ttype == "student" \
            else self.pick(["low", "medium", "high"], [0.2, 0.5, 0.3])
        pid = self.new_person("", occupation)
        tid = self.nid("tenant")
        rel = float(self.rng.beta(*c["reliability_beta"][ttype]))
        sat = float(self.rng.normal())
        self.t["tenant"].append({"tenant_id": tid, "person_id": pid, "tenant_type": ttype,
                                 "income_band": None if self.chance(nz["missing_income"]) else income})
        self.hidden.append(("tenant", tid, "reliability", round(rel, 4)))
        self.hidden.append(("tenant", tid, "satisfaction", round(sat, 4)))
        tenant = {"id": tid, "pid": pid, "rel": rel, "sat": sat, "type": ttype, "family": []}
        self.tenants[tid] = tenant
        # KYC, emergency contact, household
        self._kyc_doc(pid, "id_proof", start)
        if self.chance(c["kyc"]["address_proof_prob"]):
            self._kyc_doc(pid, "address_proof", start)
        if ttype in ("company",) or (occupation == "salaried" and self.chance(c["kyc"]["employer_letter_prob"])):
            self._kyc_doc(pid, "employer_letter", start)
        for _ in range(1 + int(self.chance(0.3))):
            self.t["emergency_contact"].append({
                "contact_id": self.nid("contact"), "person_id": pid,
                "relation": self.pick(["father", "mother", "brother", "sister", "friend", "spouse"]),
                "phone_hash": self.phone_hash()})
        self._household(tenant, start)
        return tenant

    def _household(self, tenant, start):
        t = tenant["type"]
        members = []
        if t == "family":
            if self.chance(0.85):
                members.append(("spouse", self.pick(["18-35", "36-60"], [0.55, 0.45]), self.chance(0.7)))
            for _ in range(min(3, int(self.rng.poisson(1.1)))):
                members.append(("child", self.pick(["0-12", "13-17"], [0.7, 0.3]), False))
            if self.chance(0.08):
                members.append(("parent", "60+", False))
        elif t == "bachelor":
            members += [("flatmate", "18-35", self.chance(0.3)) for _ in range(self.pick([0, 1, 2, 3], [0.4, 0.3, 0.2, 0.1]))]
        elif t == "student":
            members += [("roommate", "18-35", False) for _ in range(self.pick([0, 1, 2], [0.5, 0.35, 0.15]))]
        else:
            members += [("employee", self.pick(["18-35", "36-60"], [0.7, 0.3]), self.chance(0.5))
                        for _ in range(self.pick([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1]))]
        for rel, age, on_agr in members:
            row = {"member_id": self.nid("member"), "tenant_id": tenant["id"], "relation": rel, "age_band": age,
                   "is_on_agreement": bool(on_agr), "valid_from": start, "valid_to": None}
            self.t["family_member"].append(row)
            tenant["family"].append(row)

    # ---------- events, verification
    def _event(self, lease_id, etype, d, note=None):
        self.t["lease_event"].append({"event_id": self.nid("event"), "lease_id": lease_id, "event_type": etype,
                                      "event_date": d, "recorded_at": self.entered(d, 10), "note": note})

    def _police_verification(self, tenant, lease_id, start, valid_to, first):
        pv = self.cfg["police_verification"]
        row = {"pv_id": self.nid("pv"), "tenant_id": tenant["id"], "lease_id": lease_id, "submitted_on": None,
               "status": "not_submitted", "completed_on": None, "expiry": None}
        if self.chance(pv["submit_prob_first"] if first else pv["submit_prob_renewal"]):
            submitted = start + timedelta(days=int(self.rng.integers(0, pv["max_submit_delay_days"] + 1)))
            if submitted <= self.end:
                row["submitted_on"], row["status"] = submitted, "submitted"
                completed = submitted + timedelta(days=3 + int(self.rng.exponential(pv["review_mean_days"])))
                if completed <= self.end:
                    row["completed_on"] = completed
                    if self.chance(pv["reject_prob"]):
                        row["status"] = "rejected"
                    else:
                        row["status"] = "completed"
                        row["expiry"] = completed + timedelta(days=pv["validity_days"])
                        if row["expiry"] < self.end and valid_to > row["expiry"]:
                            row["status"] = "expired"        # lease still running after the verification lapsed
        self.t["police_verification"].append(row)

    # ---------- complaints
    def _complaints_window(self, flat, owner, tenant, w_start, w_end):
        if w_end < w_start:
            return
        days = (w_end - w_start).days + 1
        n = int(self.rng.poisson(self.cfg["complaints"]["rate_per_lease_month"] * days / 30.4))
        for _ in range(n):
            self._one_complaint(flat, owner, tenant, w_start + timedelta(days=int(self.rng.integers(0, days))))

    def _one_complaint(self, flat, owner, tenant, d):
        cc = self.cfg["complaints"]
        cats, w = cc["categories"]
        w = np.array(w, dtype=float)
        if d.month in cc["monsoon_months"]:
            w[cats.index("plumbing")] *= cc["monsoon_plumbing_boost"]
        cat = cats[int(self.rng.choice(len(cats), p=w / w.sum()))]
        prio = self.pick(["low", "medium", "high", "urgent"], cc["priority"][cat])
        resp = self.pick(cc["responsibility"][cat])
        mult = 1 + (1 - owner["resp"]) * cc["owner_slowness"] if resp == "owner" \
            else cc["society_multiplier"] if resp == "society" else 1.0
        res_days = 0.2 + float(self.rng.exponential(cc["mean_days"][prio] * mult))
        hour = int(self.pick([21, 22, 23, 0, 1])) if cat == "noise" else int(self.rng.integers(8, 22))
        created = datetime(d.year, d.month, d.day, hour, int(self.rng.integers(0, 60)))
        text = make_text(self.rng, cat, prio, flat["tower"], cc["hinglish_prob"], cc["typo_prob"])
        staff_pool = self.staff_by_role[ROLE_FOR[cat]]
        staff = staff_pool[int(self.rng.integers(len(staff_pool)))]
        rejected = self.chance(cc["reject_prob"])

        ups = [(created, "open", "Complaint registered", "tenant")]
        resolved_ts = prog_ts = None
        if rejected:
            ups.append((created + timedelta(days=float(self.rng.uniform(0.2, 2.0))), "rejected",
                        "Rejected after review by office", "office"))
        else:
            assign_ts = created + timedelta(hours=1 + float(self.rng.exponential(3)))
            resolved_ts = max(created + timedelta(days=res_days), assign_ts + timedelta(hours=2))
            prog_ts = assign_ts + (resolved_ts - assign_ts) / 2
            ups += [(assign_ts, "assigned", f"Assigned to {staff['name']}", "office"),
                    (prog_ts, "in_progress", "Work started", staff["name"]),
                    (resolved_ts, "resolved", "Issue fixed", staff["name"])]
            if self.chance(cc["close_prob"]):
                ups.append((resolved_ts + timedelta(days=float(self.rng.uniform(1, 5))), "closed",
                            "Closed after confirmation", "office"))
        ups = [u for u in ups if u[0] <= self.cutoff]          # nothing can be recorded after the snapshot
        statuses = [u[1] for u in ups]
        resolved_included = "resolved" in statuses
        cid = self.nid("complaint")
        self.t["complaint"].append({
            "complaint_id": cid, "flat_id": flat["id"], "raised_by": tenant["pid"], "category": cat,
            "priority": prio, "description_text": text, "responsibility": resp, "status": ups[-1][1],
            "created_at": ts(created), "resolved_at": ts(resolved_ts) if resolved_included else None})
        for u_ts, u_status, note, by in ups:
            self.t["complaint_update"].append({"update_id": self.nid("update"), "complaint_id": cid,
                                               "status": u_status, "note": note, "updated_at": ts(u_ts),
                                               "updated_by": by})
        if "assigned" in statuses:
            a_ts = ups[statuses.index("assigned")][0]
            end_ts = ts(resolved_ts) if resolved_included else None
            others = [s for s in staff_pool if s["id"] != staff["id"]]
            if "in_progress" in statuses and others and self.chance(0.10):
                s2 = others[int(self.rng.integers(len(others)))]
                spans = [(staff, ts(a_ts), ts(prog_ts)), (s2, ts(prog_ts), end_ts)]
            else:
                spans = [(staff, ts(a_ts), end_ts)]
            for s, f, to in spans:
                self.t["complaint_assignment"].append({"assignment_id": self.nid("assignment"), "complaint_id": cid,
                                                       "staff_id": s["id"], "assigned_from": f, "assigned_to": to})
        # effect on the tenant's hidden satisfaction
        sh = cc["sat_hit"]
        if rejected:
            tenant["sat"] -= sh["rejected"]
        elif resp in ("owner", "society", "unknown"):
            factor = 0.5 if resp == "unknown" else 1.0
            if resolved_included:
                if res_days <= sh["fast_days"]:
                    tenant["sat"] += sh["fast_bonus"] * factor
                else:
                    tenant["sat"] -= min(sh["cap"], sh["base"] + sh["per_day"] * res_days) * factor
            else:
                tenant["sat"] -= sh["unresolved"] * factor
        tenant["sat"] = float(np.clip(tenant["sat"], -4, 4))
        # money and penalties that follow from complaints
        if resp == "society" and resolved_included and cat in cc["repair_cost_logmean"] and self.chance(cc["repair_cost_prob"]):
            amount = max(200, int(round(float(self.rng.lognormal(cc["repair_cost_logmean"][cat], 0.5)) / 50.0)) * 50)
            self.pending_repairs.append({"date": resolved_ts.date(), "category": f"repair_{cat}", "amount": amount,
                                         "staff_id": staff["id"]})
        if cat in ("noise", "parking") and self.chance(cc["violation_prob"]):
            self.violations.append(d)

    # ---------- one lease: payments, complaints, how it ends
    def _run_lease(self, flat, owner, tenant, start, term, rent, deposit, notice, lock_in, parent_id):
        c, p = self.cfg, self.cfg["payment"]
        lease_id = self.nid("lease")
        sched_end = add_months(start, term)
        actual_end, outcome = sched_end, "ended"
        due = date(start.year, start.month, c["rent_due_day"])
        if due < start:
            due = add_months(due, 1)
        n_pay = n_late = 0
        unpaid = 0.0
        prev_late = False
        cursor = start                                     # complaints are generated in windows up to this date
        rel = tenant["rel"]
        while due < actual_end and due <= self.end:
            months_elapsed = (due.year - start.year) * 12 + due.month - start.month
            if outcome == "ended" and months_elapsed >= max(lock_in, 1):
                h = c["early_exit"]["base"] + c["early_exit"]["sat_coef"] * max(0.0, -tenant["sat"])
                if self.chance(h):
                    exit_day = date(due.year, due.month, int(self.rng.integers(1, 29)))
                    if start + timedelta(days=30) < exit_day < sched_end and exit_day <= self.end:
                        actual_end, outcome = exit_day, "early_exit"
            if due >= actual_end:
                break
            month_end = date(due.year, due.month, calendar.monthrange(due.year, due.month)[1])
            w_end = min(month_end, actual_end - timedelta(days=1), self.end)
            self._complaints_window(flat, owner, tenant, cursor, w_end)
            cursor = w_end + timedelta(days=1)

            late_days, paid, amount_paid = 0, True, float(rent)
            if self.chance(p["miss_coef"] * max(0.0, p["miss_rel_cutoff"] - rel)):
                paid, amount_paid = False, 0.0
            else:
                p_late = min(0.9, max(0.0, p["late_coef"] * (1 - rel) + (p["streak_bump"] if prev_late else 0.0)))
                if self.chance(p_late):
                    scale = p["late_scale_base"] + p["late_scale_slope"] * (1 - rel)
                    late_days = 1 + int(self.rng.exponential(scale))
                if self.chance(p["partial_prob"]):
                    amount_paid = round(rent * float(self.rng.uniform(0.4, 0.9)), 2)
            paid_date = due + timedelta(days=late_days) if paid else None
            if paid_date is not None and paid_date > self.end:     # not yet paid at snapshot time
                paid_date, amount_paid = None, 0.0
            n_pay += 1
            n_late += 1 if (paid_date is None or late_days > 0) else 0
            prev_late = late_days > 0 or paid_date is None
            unpaid += (rent - amount_paid) if paid_date is None or amount_paid < rent else 0.0
            ref = paid_date or due
            mode = None if paid_date is None or self.chance(self.cfg["noise"]["missing_payment_mode"]) \
                else self.pick(p["modes"])
            self.t["rent_payment"].append({
                "payment_id": self.nid("payment"), "lease_id": lease_id, "due_date": due, "paid_date": paid_date,
                "amount_due": float(rent), "amount_paid": amount_paid, "mode": mode,
                "recorded_at": self.entered(ref, 12)})
            due = add_months(due, 1)
        last = min(actual_end - timedelta(days=1), self.end)
        self._complaints_window(flat, owner, tenant, cursor, last)

        if outcome == "ended" and sched_end > self.end:
            outcome = "active"                                      # still running at the snapshot date
        valid_to = sched_end if outcome == "active" else actual_end
        self.t["lease"].append({
            "lease_id": lease_id, "parent_lease_id": parent_id, "flat_id": flat["id"], "owner_id": flat["owner_id"],
            "tenant_id": tenant["id"], "valid_from": start, "valid_to": valid_to, "monthly_rent": float(rent),
            "deposit_amount": float(deposit), "notice_days": notice, "lock_in_months": lock_in,
            "recorded_at": datetime(start.year, start.month, start.day, 12) - timedelta(days=int(self.rng.integers(0, 11))),
            "superseded_at": None})
        if parent_id is None:
            move_in = start
            if self.chance(self.cfg["noise"]["move_in_before_lease_prob"]):      # contradictory record on purpose
                move_in = start - timedelta(days=int(self.rng.integers(1, 21)))
                self.anomalies.append(("lease_event", self._ids.get("event", 0) + 1, "move_in_before_lease_start", lease_id))
            self._event(lease_id, "move_in", move_in)
        else:
            self._event(lease_id, "renewal", start)
        self._police_verification(tenant, lease_id, start, valid_to, first=parent_id is None)
        return {"lease_id": lease_id, "outcome": outcome, "end": valid_to, "sched_end": sched_end,
                "late_rate": n_late / max(1, n_pay), "unpaid": unpaid}

    # ---------- deposit settlement at the end of a tenancy
    def _settle_deposit(self, root_id, last_id, deposit, rent, end_date, early, owner, unpaid):
        dc = self.cfg["deposit"]
        deductions = []
        if unpaid > 0:
            deductions.append((min(unpaid, deposit), "unpaid rent"))
        if early and self.chance(dc["short_notice_prob"]):
            deductions.append((rent, "insufficient notice period"))
        if self.chance(dc["damage_prob_early_exit"] if early else dc["damage_prob"]):
            amt = round500(deposit * float(self.rng.uniform(*dc["damage_pct"])))
            deductions.append((amt, dc["damage_reasons"][int(self.rng.integers(len(dc["damage_reasons"])))]))
        remaining = float(deposit)
        for amt, reason in deductions:
            amt = round(min(float(amt), remaining), 2)
            if amt <= 0:
                continue
            remaining = round(remaining - amt, 2)
            self.t["deposit_ledger"].append({"entry_id": self.nid("ledger"), "lease_id": root_id,
                                             "entry_type": "deducted", "amount": amt, "entry_date": end_date,
                                             "reason": reason})
        delay = 3 + int(self.rng.exponential(dc["return_delay_base"] + (1 - owner["resp"]) * dc["return_delay_slow"]))
        back = end_date + timedelta(days=delay)
        if remaining > 0 and back <= self.end:
            self.t["deposit_ledger"].append({"entry_id": self.nid("ledger"), "lease_id": root_id,
                                             "entry_type": "returned", "amount": remaining, "entry_date": back,
                                             "reason": "balance refunded"})
            self._event(last_id, "deposit_returned", back)

    def _simulate_flat(self, flat):
        c, r = self.cfg, self.cfg["renewal"]
        owner = self.owners[flat["owner_id"]]
        avail = self.start
        while True:
            wait = int(self.rng.geometric(c["fill_prob"])) - 1
            start_month = add_months(month_start_on_or_after(avail), wait)
            day = 1 if self.chance(0.7) else int(self.rng.integers(2, 29))
            start = start_month.replace(day=day)
            if start >= self.end:
                return
            tenant = self._new_tenant(start)
            term = self.pick(c["first_term_months"])
            rent = round500(self.market_rent(flat, start) * (1 + self.rng.normal(0, 0.05)))
            deposit = rent * self.pick(c["deposit_multiple"])
            notice = self.pick(c["notice_days"])
            lock_in = min(self.pick(c["lock_in_months"]), term - 1)
            payer = "tenant" if self.chance(c["charges"]["tenant_pays_maintenance_prob"]) else "owner"
            parent, root_id, chain_unpaid = None, None, 0.0
            while True:
                res = self._run_lease(flat, owner, tenant, start, term, rent, deposit, notice, lock_in, parent)
                lid, out = res["lease_id"], res["outcome"]
                self.lease_payer[lid] = payer
                chain_unpaid += res["unpaid"]
                if parent is None:
                    root_id = lid
                    self.t["deposit_ledger"].append({"entry_id": self.nid("ledger"), "lease_id": root_id,
                                                     "entry_type": "received", "amount": float(deposit),
                                                     "entry_date": start, "reason": "security deposit"})
                if out == "active":
                    self.outcome[lid] = "active"
                    return
                if out == "early_exit":
                    self.outcome[lid] = "early_exit"
                    self._event(lid, "early_termination", max(start + timedelta(days=1), res["end"] - timedelta(days=notice)))
                    self._event(lid, "move_out", res["end"])
                    self._end_tenancy(tenant, root_id, lid, deposit, rent, res["end"], True, owner, chain_unpaid)
                    avail = res["end"]
                    break
                # natural end: owner proposes a hike, tenant decides (satisfaction now reflects complaints)
                hike = float(np.clip(self.rng.normal(owner["hike_mean"], 0.02), 0.0, 0.20))
                new_rent = round500(rent * (1 + hike))
                market_now = self.market_rent(flat, res["sched_end"])
                logit = (r["intercept"] + r["sat"] * tenant["sat"] - r["hike"] * (hike - r["base_hike"])
                         - r["late"] * res["late_rate"] - r["overpriced"] * max(0.0, new_rent / market_now - 1.05))
                if self.chance(sigmoid(logit)):
                    self.outcome[lid] = "renewed"
                    parent, start, rent = lid, res["sched_end"], new_rent
                    term, lock_in = self.pick(c["renewal_term_months"]), 0
                    if start >= self.end:
                        return
                else:
                    self.outcome[lid] = "moved_out"
                    self._event(lid, "notice_given", max(start + timedelta(days=1), res["sched_end"] - timedelta(days=notice)))
                    self._event(lid, "move_out", res["sched_end"])
                    self._end_tenancy(tenant, root_id, lid, deposit, rent, res["sched_end"], False, owner, chain_unpaid)
                    avail = res["sched_end"]
                    break

    def _end_tenancy(self, tenant, root_id, last_id, deposit, rent, end_date, early, owner, unpaid):
        for fm in tenant["family"]:
            fm["valid_to"] = end_date
        self._settle_deposit(root_id, last_id, deposit, rent, end_date, early, owner, unpaid)

    def generate(self):
        self._make_staff()
        self._make_flats()
        self._make_owners()
        for flat in self.flats:
            if flat["rentable"]:
                self._simulate_flat(flat)
        for tid, ten in self.tenants.items():
            self.hidden.append(("tenant", tid, "satisfaction_final", round(ten["sat"], 4)))
        self.t["lease"].sort(key=lambda r: r["lease_id"])
        from postpass import run_postpass
        run_postpass(self)
        return self.t


def summarize(sim):
    t = sim.t
    outs = list(sim.outcome.values())
    natural = outs.count("renewed") + outs.count("moved_out")
    paid = [r for r in t["rent_payment"] if r["paid_date"] is not None]
    late = [(r["paid_date"] - r["due_date"]).days for r in paid]
    unpaid = len(t["rent_payment"]) - len(paid)
    leases = t["lease"]
    cs = t["complaint"]
    res_h = [(r["resolved_at"] - r["created_at"]).total_seconds() / 86400 for r in cs if r["resolved_at"]]
    maint = [r for r in t["charge"] if r["charge_type"] == "maintenance" and r["paid_date"] is not None]

    def count(rows, key):
        out = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return dict(sorted(out.items()))

    an = {}
    for e, _, typ, _ in sim.anomalies:
        an[typ] = an.get(typ, 0) + 1
    return {
        "rows": {k: len(v) for k, v in t.items()},
        "flats_rentable": sum(1 for f in sim.flats if f["rentable"]),
        "lease_outcomes": {k: outs.count(k) for k in ("renewed", "moved_out", "early_exit", "active")},
        "renewal_rate_at_natural_end": round(outs.count("renewed") / natural, 3) if natural else None,
        "payments_paid_late_pct": round(100 * sum(1 for d in late if d > 0) / max(1, len(late)), 1),
        "payments_unpaid_pct": round(100 * unpaid / max(1, len(t["rent_payment"])), 2),
        "avg_days_late_when_late": round(float(np.mean([d for d in late if d > 0])), 1) if any(d > 0 for d in late) else 0,
        "avg_monthly_rent": round(float(np.mean([l["monthly_rent"] for l in leases])), 0) if leases else 0,
        "avg_deposit_multiple": round(float(np.mean([l["deposit_amount"] / l["monthly_rent"] for l in leases])), 2) if leases else 0,
        "complaints_by_status": count(cs, "status"),
        "complaints_by_priority": count(cs, "priority"),
        "complaint_mean_days_to_resolve": round(float(np.mean(res_h)), 2) if res_h else None,
        "complaint_median_days_to_resolve": round(float(np.median(res_h)), 2) if res_h else None,
        "police_verification_status": count(t["police_verification"], "status"),
        "charges_by_type": count(t["charge"], "charge_type"),
        "maintenance_paid_late_pct": round(100 * sum(1 for r in maint if r["paid_date"] > r["due_date"]) / max(1, len(maint)), 1),
        "deposit_entries": count(t["deposit_ledger"], "entry_type"),
        "anomaly_labels": an,
    }
