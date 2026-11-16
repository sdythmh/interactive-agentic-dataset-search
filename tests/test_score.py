"""Offline checks for eval/score.py and the files in benchmark/.

    python tests/test_score.py
"""
import csv
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))
import score  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-12
    print("  [%s] %-60s %r" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILED.append(label)


def load(name):
    with open(os.path.join(ROOT, "benchmark", name), encoding="utf-8") as f:
        return json.load(f)


def main():
    print("1. metrics")
    groups = [["a", "a2"], ["b"], ["c"]]
    m = score.metrics(["x", "a", "a2", "b"], groups)
    check("two of three groups found, the copy counts once", m["norm"], 2 / 3.0)
    check("MRR: first relevant at rank 2", m["mrr"], 0.5)
    idcg = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    check("nDCG: gains at ranks 2 and 4", m["ndcg"], (1 / math.log2(3) + 1 / math.log2(5)) / idcg)
    many = [[str(i)] for i in range(25)]
    check("recall is capped at ten groups", score.metrics([str(i) for i in range(10)], many)["norm"], 1.0)
    check("nothing relevant", score.metrics(["x", "y"], groups)["norm"], 0.0)

    print("2. benchmark files")
    total = 0
    for c in ("au", "uk", "us"):
        topics = load("topics_%s.json" % c)["topics"]
        gold = {t["topic"]: t for t in load("gold_%s.json" % c)["topics"]}
        check("%s: 50 topics" % c, len(topics), 50)
        check("%s: four sub-questions each" % c,
              all(sorted(t["sub_questions"]) == ["L1", "L2", "L3", "L4"] for t in topics), True)
        check("%s: one gold entry per topic" % c, sorted(gold) == sorted(t["topic"] for t in topics), True)
        check("%s: groups cover exactly the relevant records" % c,
              all(sorted({i for g in t["groups"] for i in g}) == sorted(t["relevant"])
                  for t in gold.values()), True)
        total += sum(len(t["sub_questions"]) for t in topics)
        with open(os.path.join(ROOT, "benchmark", "samples_%s.csv" % c), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        check("%s: 8,000 sample rows with id and url" % c,
              (len(rows), all(r["id"] and r["url"] for r in rows)), (8000, True))
    check("600 sub-questions in all", total, 600)
    for c, n in (("au", 12), ("uk", 6)):
        qs = load("vague_%s.json" % c)["questions"]
        topics = {t["topic"] for t in load("topics_%s.json" % c)["topics"]}
        check("%s: %d vague questions" % (c, n), len(qs), n)
        check("%s: each points at a scored topic" % c, all(q["topic"] in topics for q in qs), True)

    if FAILED:
        print("\n%d check(s) failed: %s" % (len(FAILED), "; ".join(FAILED)))
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
