"""Stage 1 simulator: society, owners, flats, tenants, leases, lease events, rent payments.

Design: each flat is simulated independently, month by month, from sim_start to sim_end.
Hidden traits (tenant reliability, tenant satisfaction, owner hike habit) DRIVE behaviour but
are never written to the database. They go to hidden_traits.csv for validation only.
"""
import calendar
import math
from datetime import date, datetime, timedelta

import numpy as np

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Rahul", "Amit", "Vikram", "Manish",
               "Ananya", "Diya", "Priya", "Neha", "Pooja", "Kavya", "Riya", "Sneha", "Isha", "Meera"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Mehta", "Jain", "Agarwal", "Chauhan", "Kapoor",
              "Reddy", "Nair", "Das", "Banerjee", "Khan", "Joshi", "Malhotra", "Bansal", "Saxena", "Tiwari"]

# Column order per table (shared by the DB loader and the CSV export)
COLUMNS = {
    "person": ["person_id", "full_name", "gender", "occupation_type", "created_at"],
    "owner": ["owner_id", "person_id", "owner_type", "join_date"],
    "tenant": ["tenant_id", "person_id", "tenant_type", "income_band"],
    "flat": ["flat_id", "tower", "floor_no", "unit_no", "bhk", "area_sqft", "furnishing"],
    "lease": ["lease_id", "parent_lease_id", "flat_id", "owner_id", "tenant_id", "valid_from", "valid_to",
              "monthly_rent", "deposit_amount", "notice_days", "lock_in_months", "recorded_at", "superseded_at"],
    "lease_event": ["event_id", "lease_id", "event_type", "event_date", "recorded_at", "note"],
    "rent_payment": ["payment_id", "lease_id", "due_date", "paid_date", "amount_due", "amount_paid", "mode",
                     "recorded_at"],
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


class Simulator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg["seed"])
        self.start, self.end = cfg["sim_start"], cfg["sim_end"]
        self.t = {name: [] for name in COLUMNS}
        self.hidden = []
        self.outcome = {}            # lease_id -> renewed | moved_out | early_exit | active  (analysis only)
        self._ids = {}
        self.owners = {}
        self.flats = []

    # ---------- small helpers
    def nid(self, name):
        self._ids[name] = self._ids.get(name, 0) + 1
        return self._ids[name]

    def pick(self, spec, probs=None):
        if probs is None:
            options, probs = spec
        else:
            options = spec
        return options[int(self.rng.choice(len(options), p=probs))]

    def chance(self, p):
        return bool(self.rng.random() < p)

    def entered(self, d, hour):
        """Timestamp when a real-world event dated d was recorded; ~20% are entered late."""
        delay = int(self.rng.integers(1, self.cfg["late_entry_max_days"] + 1)) \
            if self.chance(self.cfg["late_entry_prob"]) else 0
        return datetime(d.year, d.month, d.day, hour) + timedelta(days=delay)

    def new_person(self, name_suffix="", occupation="salaried"):
        pid = self.nid("person")
        gender = "M" if self.chance(0.55) else "F"
        first = FIRST_NAMES[int(self.rng.integers(len(FIRST_NAMES)))]
        last = LAST_NAMES[int(self.rng.integers(len(LAST_NAMES)))]
        name = f"{first} {last}{name_suffix}"
        self.t["person"].append({"person_id": pid, "full_name": name, "gender": gender,
                                 "occupation_type": occupation,
                                 "created_at": datetime(self.start.year, self.start.month, self.start.day)})
        return pid

    def market_rent(self, flat, d):
        c = self.cfg
        years = (d - self.start).days / 365.25
        rent = (c["rent_per_sqft_start"] * (1 + c["rent_annual_growth"]) ** years * flat["area"]
                * flat["quality"] * c["furnishing_premium"][flat["furn"]]
                * (1 + c["floor_premium_per_floor"] * (flat["floor"] - 1)))
        return rent

    # ---------- society
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
                                           "furnishing": furn})

    def _make_owners(self):
        c = self.cfg
        n_owners = max(1, int(len(self.flats) * c["owner_ratio"]))
        order = self.rng.permutation(len(self.flats))
        for i in range(n_owners):
            otype = self.pick(c["owner_types"])
            suffix = " Holdings" if otype == "company" else ""
            pid = self.new_person(suffix, "business" if otype == "company" else "salaried")
            oid = self.nid("owner")
            hike_mean = float(np.clip(self.rng.normal(*c["owner_hike_mean"]), 0.0, 0.15))
            self.owners[oid] = {"id": oid, "type": otype, "hike_mean": hike_mean}
            join = self.start - timedelta(days=int(self.rng.integers(0, 1500)))
            self.t["owner"].append({"owner_id": oid, "person_id": pid, "owner_type": otype, "join_date": join})
            self.hidden.append(("owner", oid, "hike_mean", round(hike_mean, 4)))
        for rank, fi in enumerate(order):
            oid = rank + 1 if rank < n_owners else int(self.rng.integers(1, n_owners + 1))
            flat = self.flats[int(fi)]
            flat["owner_id"] = oid
            flat["rentable"] = self.chance(c["rentable_prob"][self.owners[oid]["type"]])

    def _new_tenant(self):
        c = self.cfg
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
        self.t["tenant"].append({"tenant_id": tid, "person_id": pid, "tenant_type": ttype, "income_band": income})
        self.hidden.append(("tenant", tid, "reliability", round(rel, 4)))
        self.hidden.append(("tenant", tid, "satisfaction", round(sat, 4)))
        return {"id": tid, "rel": rel, "sat": sat}

    # ---------- events
    def _event(self, lease_id, etype, d, note=None):
        self.t["lease_event"].append({"event_id": self.nid("event"), "lease_id": lease_id, "event_type": etype,
                                      "event_date": d, "recorded_at": self.entered(d, 10), "note": note})

    # ---------- one lease: payments + how it ends
    def _run_lease(self, flat, tenant, start, term, rent, deposit, notice, lock_in, parent_id):
        c, p = self.cfg, self.cfg["payment"]
        lease_id = self.nid("lease")
        sched_end = add_months(start, term)
        actual_end, outcome = sched_end, "ended"
        due = date(start.year, start.month, c["rent_due_day"])
        if due < start:
            due = add_months(due, 1)
        n_pay = n_late = 0
        prev_late = False
        rel, sat = tenant["rel"], tenant["sat"]
        while due < actual_end and due <= self.end:
            months_elapsed = (due.year - start.year) * 12 + due.month - start.month
            if outcome == "ended" and months_elapsed >= max(lock_in, 1):
                h = c["early_exit"]["base"] + c["early_exit"]["sat_coef"] * max(0.0, -sat)
                if self.chance(h):
                    exit_day = date(due.year, due.month, int(self.rng.integers(1, 29)))
                    if start + timedelta(days=30) < exit_day < sched_end and exit_day <= self.end:
                        actual_end, outcome = exit_day, "early_exit"
            if due >= actual_end:
                break
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
            ref = paid_date or due
            self.t["rent_payment"].append({
                "payment_id": self.nid("payment"), "lease_id": lease_id, "due_date": due, "paid_date": paid_date,
                "amount_due": float(rent), "amount_paid": amount_paid,
                "mode": self.pick(p["modes"]) if paid_date is not None else None,
                "recorded_at": self.entered(ref, 12)})
            due = add_months(due, 1)

        if outcome == "ended" and sched_end > self.end:
            outcome = "active"                                      # still running at the snapshot date
        valid_to = sched_end if outcome == "active" else actual_end
        self.t["lease"].append({
            "lease_id": lease_id, "parent_lease_id": parent_id, "flat_id": flat["id"], "owner_id": flat["owner_id"],
            "tenant_id": tenant["id"], "valid_from": start, "valid_to": valid_to, "monthly_rent": float(rent),
            "deposit_amount": float(deposit), "notice_days": notice, "lock_in_months": lock_in,
            "recorded_at": datetime(start.year, start.month, start.day, 12) - timedelta(days=int(self.rng.integers(0, 11))),
            "superseded_at": None})
        self._event(lease_id, "move_in" if parent_id is None else "renewal", start)
        return {"lease_id": lease_id, "outcome": outcome, "end": valid_to, "sched_end": sched_end,
                "late_rate": n_late / max(1, n_pay)}

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
            tenant = self._new_tenant()
            term = self.pick(c["first_term_months"])
            rent = round500(self.market_rent(flat, start) * (1 + self.rng.normal(0, 0.05)))
            deposit = rent * self.pick(c["deposit_multiple"])
            notice = self.pick(c["notice_days"])
            lock_in = min(self.pick(c["lock_in_months"]), term - 1)
            parent = None
            while True:
                res = self._run_lease(flat, tenant, start, term, rent, deposit, notice, lock_in, parent)
                lid, out = res["lease_id"], res["outcome"]
                if out == "active":
                    self.outcome[lid] = "active"
                    return
                if out == "early_exit":
                    self.outcome[lid] = "early_exit"
                    self._event(lid, "early_termination", max(start + timedelta(days=1), res["end"] - timedelta(days=notice)))
                    self._event(lid, "move_out", res["end"])
                    avail = res["end"]
                    break
                # natural end: owner proposes a hike, tenant decides
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
                    avail = res["sched_end"]
                    break

    def generate(self):
        self._make_flats()
        self._make_owners()
        for flat in self.flats:
            if flat["rentable"]:
                self._simulate_flat(flat)
        # tables in a stable order for reproducible files
        self.t["lease"].sort(key=lambda r: r["lease_id"])
        return self.t


def summarize(sim):
    t = sim.t
    outs = list(sim.outcome.values())
    natural = outs.count("renewed") + outs.count("moved_out")
    paid = [r for r in t["rent_payment"] if r["paid_date"] is not None]
    late = [(r["paid_date"] - r["due_date"]).days for r in paid]
    unpaid = len(t["rent_payment"]) - len(paid)
    leases = t["lease"]
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
    }
