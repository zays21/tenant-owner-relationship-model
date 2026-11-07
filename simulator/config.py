"""All simulator assumptions live here. Change a number, regenerate, compare results.
Every value is an ASSUMPTION, not a fact: document the ones you keep in your paper.
"""
from datetime import date

CONFIG = {
    "seed": 42,                       # same seed + same config => identical dataset
    "version": "stage1-v0.1",
    "sim_start": date(2018, 1, 1),
    "sim_end": date(2026, 10, 31),    # "today" inside the simulation

    # ---- society layout (towers x floors x units per floor, trimmed to n_flats)
    "n_flats": 500,
    "floors": 10,
    "units_per_floor": 10,

    # ---- owners
    "owner_ratio": 0.78,              # owners per flat (some own 2+ flats)
    "owner_types": (["resident", "absentee", "company"], [0.35, 0.50, 0.15]),
    "rentable_prob": {"resident": 0.25, "absentee": 0.92, "company": 1.0},
    "owner_hike_mean": (0.06, 0.025), # owner's usual rent hike at renewal: mean, sd

    # ---- flats
    "bhk": ([1, 2, 3, 4], [0.15, 0.40, 0.35, 0.10]),
    "area_by_bhk": {1: 650, 2: 1050, 3: 1450, 4: 2000},
    "furnishing": (["unfurnished", "semi", "furnished"], [0.40, 0.40, 0.20]),
    "furnishing_premium": {"unfurnished": 1.00, "semi": 1.06, "furnished": 1.15},

    # ---- market rent: replace with numbers calibrated from a real rent dataset
    "rent_per_sqft_start": 22.0,      # Rs per sq ft per month in sim_start year
    "rent_annual_growth": 0.06,
    "floor_premium_per_floor": 0.004,

    # ---- vacancy and lease terms
    "fill_prob": 0.40,                # chance per month that a vacant rentable flat gets a tenant
    "first_term_months": ([11, 12, 24], [0.80, 0.12, 0.08]),
    "renewal_term_months": ([11, 12], [0.90, 0.10]),
    "deposit_multiple": ([1, 2, 3], [0.20, 0.50, 0.30]),
    "notice_days": ([30, 60, 90], [0.50, 0.35, 0.15]),
    "lock_in_months": ([0, 3, 6, 11], [0.20, 0.15, 0.45, 0.20]),
    "rent_due_day": 5,

    # ---- tenants
    "tenant_types": (["family", "bachelor", "student", "company"], [0.45, 0.25, 0.12, 0.18]),
    # hidden reliability ~ Beta(a, b) by tenant type (NOT stored as a column)
    "reliability_beta": {"family": (5, 2), "bachelor": (4, 2), "student": (3.5, 2), "company": (6, 2)},

    # ---- rent payment behaviour
    "payment": {
        "late_coef": 0.90,            # P(late) ~ late_coef * (1 - reliability)
        "streak_bump": 0.12,          # extra P(late) if last month was late
        "late_scale_base": 2.0,       # mean extra days late = base + slope*(1-reliability)
        "late_scale_slope": 12.0,
        "miss_coef": 0.06,            # P(unpaid) = coef * max(0, cutoff - reliability)
        "miss_rel_cutoff": 0.60,
        "partial_prob": 0.03,
        "modes": (["upi", "bank_transfer", "cheque", "cash"], [0.60, 0.30, 0.07, 0.03]),
    },

    # ---- how a lease ends
    "early_exit": {"base": 0.004, "sat_coef": 0.012},   # monthly hazard after the lock-in
    "renewal": {                       # logit = intercept + sat*s - hike*(h-base_hike) - late*late_rate - overpriced*max(0, rent/market-1.05)
        "intercept": 0.9, "sat": 0.8, "hike": 12.0, "late": 1.5, "overpriced": 3.0, "base_hike": 0.05,
    },

    # ---- data-entry realism: share of records entered late, and how late
    "late_entry_prob": 0.20,
    "late_entry_max_days": 10,
}
