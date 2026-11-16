"""Write the input files eval/run_eval.py, eval/run_methods.py and
eval/run_vague.py read, from benchmark/.

    python eval/benchmark_input.py --corpus au --out runs/au
"""
import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmark")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, choices=("au", "uk", "us"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    c = args.corpus
    topics = load(os.path.join(BENCH, "topics_%s.json" % c))["topics"]
    gold = {t["topic"]: t for t in load(os.path.join(BENCH, "gold_%s.json" % c))["topics"]}
    needs = []
    for t in topics:
        g = gold[t["topic"]]
        needs.append({"id": t["topic"],
                      # topic ids are <kind>-<number>, e.g. publisher-03
                      "category": t["topic"].rsplit("-", 1)[0],
                      "phrasings": t["sub_questions"],
                      "gold": g["relevant"],
                      "gold_groups": g["groups"]})
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "topics.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"corpus": c, "corpus_fingerprint": "benchmark/samples_%s.csv" % c,
                   "corpus_size": sum(1 for _ in open(os.path.join(BENCH, "samples_%s.csv" % c),
                                                      encoding="utf-8")) - 1,
                   "needs": needs}, f, ensure_ascii=False, indent=1)
    print("wrote %s (%d topics)" % (path, len(needs)))
    vague_path = os.path.join(BENCH, "vague_%s.json" % c)
    if os.path.exists(vague_path):
        items = [{"id": q["id"], "resolves_to": q["topic"], "vague": q["question"],
                  "withheld": q["withheld"], "withheld_dim": q["withheld_dim"]}
                 for q in load(vague_path)["questions"]]
        path = os.path.join(args.out, "vague.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"corpus": c, "items": items}, f, ensure_ascii=False, indent=1)
        print("wrote %s (%d vague questions)" % (path, len(items)))


if __name__ == "__main__":
    main()
