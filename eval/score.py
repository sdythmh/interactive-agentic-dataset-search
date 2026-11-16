"""Score the method, ablation and clarification tables against benchmark/.

    python eval/score.py --runs runs.json [--out scores.json]
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))
import paired_test as pt  # noqa: E402
import ask_report  # noqa: E402

REPS, ALPHA, SEED = 10000, 0.05, 20260914
CORPORA = ["au", "uk", "us"]
LEVELS = ["L1", "L2", "L3", "L4"]
BENCH = os.path.join(ROOT, "benchmark")

TABLE_A = ["A1", "A2", "A3", "A4"]
TABLE_B = ["B1", "B2", "B3"]
METHODS = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3"]
NAMES = {"A1": "Keyword only (BM25)", "A2": "Dense retrieval only", "A3": "Dense + LLM re-ranking",
         "A4": "Keyword + dense + cross-encoder", "A5": "IADS (full system)",
         "B1": "- cross-encoder re-ranking", "B2": "- publisher linking",
         "B3": "- decision loop (one round)"}
METRICS = [("norm", "R@10"), ("ndcg", "nDCG@10"), ("mrr", "MRR@10")]


def holm(ps):
    """{id: p} -> {id: Holm-adjusted p} (step-down, monotone)."""
    items = sorted(ps.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gold_topics(c):
    """{topic: {"relevant": [...], "groups": [[...], ...]}} in benchmark order,
    and the list of scored topics."""
    g = load(os.path.join(BENCH, "gold_%s.json" % c))
    return {t["topic"]: t for t in g["topics"]}, [t["topic"] for t in g["topics"]]


def rows_for(manifest, c, method, scored):
    out = {}
    for src in manifest[c][method]:
        want = set(src.get("topics") or scored)
        for rec in load(src["file"])["records"]:
            if rec["config"] != src["config"] or rec["need"] not in want or rec["level"] not in LEVELS:
                continue
            key = (rec["need"], rec["level"])
            assert key not in out, (method, c, key)
            out[key] = rec
    return out


# ---- metrics ---------------------------------------------------------------
def metrics(retrieved, groups):
    top = list(retrieved or [])[:10]
    gid = {}
    for i, g in enumerate(groups):
        for e in g:
            gid.setdefault(e, i)
    seen, gains = set(), []
    for e in top:
        k = gid.get(e)
        if k is not None and k not in seen:
            seen.add(k)
            gains.append(1)
        else:
            gains.append(0)
    cap = min(10, len(groups))
    norm = len(seen) / float(cap) if cap else None
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    idcg = sum(1 / math.log2(i + 2) for i in range(cap))
    ndcg = dcg / idcg if idcg else None
    gold = set(gid)
    mrr = 0.0
    for i, e in enumerate(top, start=1):
        if e in gold:
            mrr = 1.0 / i
            break
    return {"norm": norm, "ndcg": ndcg, "mrr": mrr if cap else None,
            "success": (1.0 if seen else 0.0) if cap else None}


def groups_of(topic):
    return topic.get("groups") or [[i] for i in topic["relevant"]]


def need_means(rows, gold, key):
    """{topic: mean of the metric over the topic's sub-questions}."""
    per = defaultdict(list)
    for (topic, lvl), rec in rows.items():
        per[topic].append(metrics(rec.get("retrieved"), groups_of(gold[topic]))[key])
    return {n: sum(v) / len(v) for n, v in per.items()}


def f3(x):
    return "n/a" if x is None or x != x else "%.3f" % x


def pf(p):
    return "n/a" if p != p else ("<0.001" if p < 0.001 else "%.3f" % p)


def score(manifest):
    out = {"cells": {}, "diffs": {}, "rows": {}}
    golds, all_rows = {}, {}
    for c in CORPORA:
        golds[c], scored = gold_topics(c)
        keys = {(n, l) for n in scored for l in LEVELS}
        for m in METHODS:
            rows = rows_for(manifest, c, m, scored)
            assert set(rows) == keys, (c, m, len(rows), len(keys - set(rows)))
            out["rows"]["%s|%s" % (c, m)] = {"rows": len(rows),
                                             "error_rows": sum(1 for r in rows.values() if r.get("error"))}
            all_rows[(c, m)] = rows
            for key, _ in METRICS:
                nm = need_means(rows, golds[c], key)
                out["cells"]["%s|%s|%s" % (c, m, key)] = sum(nm.values()) / len(nm)
    # paired differences to A5
    for fam, members in (("A", TABLE_A), ("B", TABLE_B)):
        ps = {}
        for c in CORPORA:
            for m in members:
                for key, _ in METRICS:
                    a5 = need_means(all_rows[(c, "A5")], golds[c], key)
                    xm = need_means(all_rows[(c, m)], golds[c], key)
                    v = [xm[n] - a5[n] for n in sorted(a5)]
                    lo, hi = pt.bootstrap_ci(v, REPS, ALPHA, SEED)
                    _, wp = pt.wilcoxon(v)
                    k = "%s|%s|%s" % (c, m, key)
                    out["diffs"][k] = {"family": fam, "mean": sum(v) / len(v), "ci": [lo, hi],
                                       "wilcoxon_p": wp, "n_topics": len(v),
                                       "nonzero": sum(1 for x in v if x != 0)}
                    ps[k] = wp if wp == wp else 1.0
        for k, p in holm(ps).items():
            out["diffs"][k]["holm_p"] = p
            out["diffs"][k]["family_size"] = len(ps)
    # clarification
    out["clarify"] = {}
    tables = []
    for c in ["au", "uk"]:
        conv = {}
        for k in ("C1", "C2", "C3"):
            src = manifest["clarify"][c][k]
            conv[k] = {}
            for r in load(src["file"])["records"]:
                if r["config"] != src["config"]:
                    continue
                mm = metrics(r.get("retrieved"), groups_of(golds[c][r["resolves_to"]]))
                conv[k][r["need"]] = dict(r, **{"recall@10_norm": mm["norm"]})
        qs = sorted(set(conv["C1"]) & set(conv["C2"]) & set(conv["C3"]))
        t = [(c, n, conv["C1"][n]["resolves_to"], conv["C1"][n], conv["C2"][n], conv["C3"][n]) for n in qs]
        tables += t
        out["clarify"][c] = {"n": len(t), "C1": sum(x[3]["recall@10_norm"] for x in t) / len(t),
                             "C2": sum(x[4]["recall@10_norm"] for x in t) / len(t),
                             "C3": sum(x[5]["recall@10_norm"] for x in t) / len(t),
                             "recovery": ask_report.recovery(t)}
    out["clarify"]["pooled"] = {"n": len(tables),
                                "C1": sum(x[3]["recall@10_norm"] for x in tables) / len(tables),
                                "C2": sum(x[4]["recall@10_norm"] for x in tables) / len(tables),
                                "C3": sum(x[5]["recall@10_norm"] for x in tables) / len(tables),
                                "recovery": ask_report.recovery(tables)}
    return out


def render(out):
    L = ["# Tables", ""]
    head = ("| Method | " + " | ".join("%s %s" % (c.upper(), lab) for c in CORPORA for _, lab in METRICS)
            + " |\n|" + "---|" * 10)
    for title, members in (("Table A: methods", TABLE_A + ["A5"]),
                           ("Table B: ablations", ["A5"] + TABLE_B)):
        L += ["## " + title, "", head]
        for m in members:
            L.append("| %s %s | " % (m, NAMES[m]) + " | ".join(
                f3(out["cells"]["%s|%s|%s" % (c, m, k)]) for c in CORPORA for k, _ in METRICS) + " |")
        L.append("")
    for title, members in (("Table A: difference to A5 [95% topic-level CI], Holm p (36 tests)", TABLE_A),
                           ("Table B: difference to A5 [95% topic-level CI], Holm p (27 tests)", TABLE_B)):
        L += ["## " + title, "", head]
        for m in members:
            cells = []
            for c in CORPORA:
                for k, _ in METRICS:
                    d = out["diffs"]["%s|%s|%s" % (c, m, k)]
                    cells.append("%+.3f [%+.3f, %+.3f], p_H %s" % (d["mean"], d["ci"][0], d["ci"][1], pf(d["holm_p"])))
            L.append("| %s %s | " % (m, NAMES[m]) + " | ".join(cells) + " |")
        L.append("")
    L += ["## Clarification (vague questions, Recall@10)", "",
          "| portal | n | C1 no question | C2 asked, simulated answer | C3 detail given | (C2-C1)/(C3-C1) [95% CI] |",
          "|---|---|---|---|---|---|"]
    for c in ["au", "uk", "pooled"]:
        x = out["clarify"][c]
        r = x["recovery"]
        rs = "n/a (denominator CI reaches 0)" if r["R"] is None else "%.3f [%s]" % (
            r["R"], "n/a" if not r["R_ci"] else "%.3f, %.3f" % tuple(r["R_ci"]))
        L.append("| %s | %d | %.3f | %.3f | %.3f | %s |" % (c, x["n"], x["C1"], x["C2"], x["C3"], rs))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", required=True, help="JSON file naming the run file of each method")
    ap.add_argument("--out", default="", help="also write the numbers as JSON here")
    args = ap.parse_args()
    out = score(load(args.runs))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, default=str)
    print(render(out))


if __name__ == "__main__":
    main()
