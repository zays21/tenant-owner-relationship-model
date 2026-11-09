"""Larger society, several seeds, anchored config. M1 is the focus (small test set at 500 flats)."""
import sys, time
import recovery_study as r
flats, seeds, tasks = int(sys.argv[1]), [int(s) for s in sys.argv[2].split(",")], sys.argv[3]
r.CONFIG["n_flats"] = flats
r.OUT = r.os.path.join(r.HERE, f"runs_{flats}")
orig = r.subprocess.run
def run(cmd, **kw):
    cmd = [("m1" if tasks == "m1" else "m1,m2") if c == "m1,m2" or c == "m1" else c for c in cmd]
    return orig(cmd, **kw)
r.subprocess.run = run
for s in seeds:
    t = time.time(); print("seed", s, flush=True)
    r.one_run(1.0, 1.0, s)
    print("done", s, round(time.time() - t), "s", flush=True)
