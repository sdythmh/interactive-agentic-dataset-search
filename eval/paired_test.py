"""Paired statistics used by eval/score.py."""
import math
import random


def bootstrap_ci(diffs, reps, alpha, seed):
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(reps):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / float(n))
    means.sort()
    lo = means[int((alpha / 2.0) * reps)]
    hi = means[min(reps - 1, int((1 - alpha / 2.0) * reps))]
    return lo, hi


def wilcoxon(diffs):
    """Signed-rank, normal approximation with tie correction."""
    nz = [d for d in diffs if d != 0]
    n = len(nz)
    if n < 6:
        return float("nan"), float("nan")
    order = sorted(range(n), key=lambda i: abs(nz[i]))
    ranks = [0.0] * n
    tie_term = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(nz[order[j + 1]]) == abs(nz[order[i]]):
            j += 1
        average = (i + j) / 2.0 + 1
        size = j - i + 1
        tie_term += size ** 3 - size
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    w_plus = sum(ranks[i] for i in range(n) if nz[i] > 0)
    mu = n * (n + 1) / 4.0
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0 - tie_term / 48.0)
    if sigma == 0:
        return w_plus, float("nan")
    z = (w_plus - mu) / sigma
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, min(1.0, p)


