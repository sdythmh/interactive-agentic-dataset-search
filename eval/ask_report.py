"""The recovery ratio of the clarification evaluation, used by eval/score.py."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from paired_test import bootstrap_ci  # noqa: E402

REPS = 10000
SEED = 20260914
ALPHA = 0.05


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / float(len(xs)) if xs else float("nan")


def recovery(table, metric="recall@10_norm"):
    """Point R, its bootstrap CI over needs, the denominator's CI, and the
    two differences with their CIs. R is None when the denominator's CI
    reaches <= 0."""
    if not table:
        return None
    num = [t[4][metric] - t[3][metric] for t in table]
    den = [t[5][metric] - t[3][metric] for t in table]
    n = len(table)
    d_lo, d_hi = bootstrap_ci(den, REPS, ALPHA, SEED)
    n_lo, n_hi = bootstrap_ci(num, REPS, ALPHA, SEED)
    out = {"n": n, "ask_minus_noask": mean(num), "ask_minus_noask_ci": (n_lo, n_hi),
           "oracle_minus_noask": mean(den), "oracle_minus_noask_ci": (d_lo, d_hi),
           "R": None, "R_ci": None, "R_dropped": 0}
    if d_lo <= 0 or mean(den) <= 0:
        return out
    out["R"] = mean(num) / mean(den)
    rng = random.Random(SEED)
    ratios, dropped = [], 0
    for _ in range(REPS):
        idx = [rng.randrange(n) for _ in range(n)]
        dn = sum(den[i] for i in idx)
        if dn <= 0:
            dropped += 1
            continue
        ratios.append(sum(num[i] for i in idx) / dn)
    ratios.sort()
    if ratios:
        out["R_ci"] = (ratios[int(ALPHA / 2 * len(ratios))],
                       ratios[min(len(ratios) - 1,
                                  int((1 - ALPHA / 2) * len(ratios)))])
    out["R_dropped"] = dropped
    return out


