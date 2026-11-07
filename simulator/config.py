"""All simulator assumptions live here. Change a number, regenerate, compare results.
Every value is an ASSUMPTION, not a fact: document the ones you keep in your paper.
"""
from datetime import date

CONFIG = {
    "seed": 42,                       # same seed + same config => identical dataset
    "version": "stage2-v0.2",
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

    # =================== STAGE 2 ===================
    # ---- complaints
    "complaints": {
        "rate_per_lease_month": 0.18,
        "monsoon_months": [6, 7, 8, 9], "monsoon_plumbing_boost": 2.0,
        "categories": (["plumbing", "electrical", "noise", "parking", "lift", "security", "cleanliness", "other"],
                       [0.24, 0.17, 0.14, 0.10, 0.09, 0.06, 0.12, 0.08]),
        # P(low, medium, high, urgent) by category
        "priority": {"plumbing": [0.10, 0.40, 0.35, 0.15], "electrical": [0.10, 0.35, 0.35, 0.20],
                     "noise": [0.50, 0.40, 0.09, 0.01], "parking": [0.50, 0.40, 0.10, 0.00],
                     "lift": [0.10, 0.30, 0.40, 0.20], "security": [0.15, 0.35, 0.35, 0.15],
                     "cleanliness": [0.50, 0.40, 0.09, 0.01], "other": [0.50, 0.40, 0.09, 0.01]},
        # who is expected to fix it: (owner, society, tenant, unknown)
        "responsibility": {"plumbing": (["owner", "society", "tenant", "unknown"], [0.55, 0.25, 0.12, 0.08]),
                           "electrical": (["owner", "society", "tenant", "unknown"], [0.50, 0.30, 0.12, 0.08]),
                           "noise": (["owner", "society", "tenant", "unknown"], [0.05, 0.60, 0.05, 0.30]),
                           "parking": (["owner", "society", "tenant", "unknown"], [0.02, 0.70, 0.03, 0.25]),
                           "lift": (["owner", "society", "tenant", "unknown"], [0.00, 0.95, 0.00, 0.05]),
                           "security": (["owner", "society", "tenant", "unknown"], [0.00, 0.95, 0.00, 0.05]),
                           "cleanliness": (["owner", "society", "tenant", "unknown"], [0.00, 0.90, 0.05, 0.05]),
                           "other": (["owner", "society", "tenant", "unknown"], [0.15, 0.60, 0.10, 0.15])},
        "mean_days": {"low": 6.0, "medium": 3.0, "high": 1.5, "urgent": 0.6},   # mean resolution time by priority
        "owner_slowness": 4.0,            # owner-fixed complaints take x(1 + (1-responsiveness)*this) longer
        "society_multiplier": 1.2,
        "reject_prob": 0.03,
        "close_prob": 0.40,
        "hinglish_prob": 0.10, "typo_prob": 0.08,
        # effect of a complaint on the tenant's hidden satisfaction
        "sat_hit": {"base": 0.10, "per_day": 0.03, "cap": 0.9, "fast_bonus": 0.08, "fast_days": 2.0,
                    "unresolved": 0.5, "rejected": 0.05},
        "repair_cost_prob": 0.6,          # society-fixed complaints that create a repair expense
        "repair_cost_logmean": {"plumbing": 7.3, "electrical": 7.2, "lift": 9.0, "security": 7.0,
                                "cleanliness": 6.5, "other": 7.0},
        "violation_prob": 0.08,           # noise/parking complaints that end in a penalty for the offender
    },

    # ---- staff and expenses
    "staff": {"plumber": 2, "electrician": 2, "guard": 4, "housekeeping": 3, "manager": 1, "vendor": 2},
    "salary": {"guard": 16000, "housekeeping": 12000, "manager": 35000},
    "salary_growth": 0.05,

    # ---- society charges
    "charges": {
        "maintenance_per_sqft": 2.6, "maintenance_growth": 0.04, "maintenance_due_day": 10,
        "tenant_pays_maintenance_prob": 0.45,
        "owner_reliability_beta": (6, 2),
        "late_coef": 0.55, "late_scale_base": 2.0, "late_scale_slope": 8.0, "p_unpaid_low_rel": 0.01,
        "penalty_after_days": 15, "penalty_pct": 0.05, "penalty_min": 200,
        "special_events_per_year": ([1, 2], [0.6, 0.4]), "special_per_sqft": (1.5, 4.0),
        "special_reasons": ["lift modernisation fund", "building repainting", "water tank replacement",
                            "generator upgrade", "CCTV upgrade"],
        "donation_prob": 0.45, "donation_amounts": ([501, 1100, 2100, 5100], [0.30, 0.40, 0.25, 0.05]),
        "donation_month": 10,
        "violation_amounts": ([500, 1000, 2000], [0.5, 0.35, 0.15]),
    },

    # ---- deposits, verification, KYC
    "deposit": {"damage_prob": 0.35, "damage_prob_early_exit": 0.50, "damage_pct": (0.05, 0.30),
                "short_notice_prob": 0.50, "return_delay_base": 10, "return_delay_slow": 30,
                "damage_reasons": ["wall repainting", "cleaning charges", "fixtures damage", "pest control"]},
    "police_verification": {"submit_prob_first": 0.90, "submit_prob_renewal": 0.50, "max_submit_delay_days": 45,
                            "review_mean_days": 15, "reject_prob": 0.02, "validity_days": 365},
    "kyc": {"id_status": (["verified", "pending", "rejected", "expired"], [0.90, 0.06, 0.02, 0.02]),
            "address_proof_prob": 0.70, "employer_letter_prob": 0.60},

    # ---- noise and anomalies (the anomalies are labelled in anomaly_labels.csv, never in the database)
    "noise": {"missing_income": 0.05, "missing_occupation": 0.03, "missing_gender": 0.02,
              "missing_furnishing": 0.02, "missing_payment_mode": 0.01, "missing_doc_number": 0.05,
              "duplicate_person_prob": 0.01, "move_in_before_lease_prob": 0.005},
    "anomalies": {"expense_inflated": 0.015, "expense_duplicate": 0.008, "expense_missing_approval": 0.005,
                  "payment_correction": 0.025, "suspicious_edit": 0.001},
}
