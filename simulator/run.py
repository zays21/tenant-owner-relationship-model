"""Generate the stage 1 dataset, save CSV snapshots, and (optionally) load it into PostgreSQL.

Examples
  python run.py --dry-run                 # generate + CSVs only, no database needed
  python run.py --reset                   # wipe tenant_ert tables, then load a fresh dataset
  python run.py --reset --flats 2000 --seed 7
"""
import argparse
import csv
import json
import math
import os
from copy import deepcopy

from config import CONFIG
from generator import COLUMNS, Simulator, summarize


def parse():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, help="random seed (default from config.py)")
    ap.add_argument("--flats", type=int, help="number of flats (default from config.py)")
    ap.add_argument("--dry-run", action="store_true", help="do not touch the database")
    ap.add_argument("--reset", action="store_true", help="TRUNCATE all tenant_ert tables before loading")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt for --reset")
    ap.add_argument("--out", default="../data/snapshots", help="folder for CSV snapshots")
    ap.add_argument("--no-csv", action="store_true")
    return ap.parse_args()


def write_snapshot(sim, cfg, stats, out_root):
    folder = os.path.join(out_root, f"{cfg['version']}_seed{cfg['seed']}_flats{cfg['n_flats']}")
    os.makedirs(folder, exist_ok=True)
    for name, cols in COLUMNS.items():
        with open(os.path.join(folder, f"{name}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in sim.t[name]:
                w.writerow(["" if r[c] is None else r[c] for c in cols])
    with open(os.path.join(folder, "hidden_traits.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entity", "entity_id", "trait", "value"])
        w.writerows(sim.hidden)                  # ground truth for validation ONLY, never a model feature
    with open(os.path.join(folder, "data_card.json"), "w", encoding="utf-8") as f:
        json.dump({"config": cfg, "stats": stats}, f, indent=2, default=str)
    return folder


def main():
    a = parse()
    cfg = deepcopy(CONFIG)
    if a.seed is not None:
        cfg["seed"] = a.seed
    if a.flats is not None:
        cfg["n_flats"] = a.flats
    sim = Simulator(cfg)
    sim.generate()
    stats = summarize(sim)
    print("Generated dataset:")
    print(json.dumps(stats, indent=2))
    if not a.no_csv:
        print("CSV snapshot saved to:", os.path.abspath(write_snapshot(sim, cfg, stats, a.out)))
    if a.dry_run:
        return
    import loader
    if a.reset and not a.yes:
        if input("This DELETES all data in the tenant_ert tables. Type yes to continue: ").strip().lower() != "yes":
            raise SystemExit("Cancelled.")
    conn = loader.connect()
    loader.load(conn, sim.t, reset=a.reset)
    print("Loaded into PostgreSQL. Database check:")
    print(json.dumps(loader.verify(conn), indent=2, default=str))


if __name__ == "__main__":
    main()
