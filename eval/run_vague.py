"""Clarification evaluation: run the vague questions under one condition and
score them against the relevant records of the topic each one comes from.

Usage (inside a corpus environment, see eval/run_clear.sh)
-----
    python eval/run_vague.py --vague runs/au/vague.json --gold runs/au/topics.json \\
        --condition ask --configs d_agent --out runs/au/vague_ask.json

--vague and --gold are the files eval/benchmark_input.py writes.
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import run_eval  # noqa: E402  (sets up sys.path and imports the system)
from run_eval import (CONFIGS, ITERATIONS, mrr_at_k, recall_at_k,  # noqa: E402
                      recall_norm, routing_scores)
import config  # noqa: E402
import hyde  # noqa: E402
import pipeline  # noqa: E402
import rerank  # noqa: E402
import retrieval  # noqa: E402
import rewrite  # noqa: E402
import user_sim  # noqa: E402

CONDITIONS = ("noask", "ask", "oracle")


def question_for(condition, item):
    """The question sent to the system under a condition."""
    if condition == "oracle":
        return item["vague"] + "; " + item["withheld"]
    return item["vague"]


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_row(name, condition, item, question):
    """(retrieved, methods, error, ask fields or None, ask fallbacks,
    clarify fallbacks)."""
    ITERATIONS["count"] = 1
    rewrite.reset_trace()
    rerank.reset_trace()
    retrieval.reset_hydration_trace()
    hyde.reset_trace()
    pipeline.reset_ask_trace()
    pipeline.reset_clarify_trace()
    user_sim.take_choice_trace()
    run_eval.ASK_ROW.clear()
    asking = condition == "ask" and name == "d_agent"
    try:
        if asking:
            vague, withheld = item["vague"], item["withheld"]
            retrieved, methods = CONFIGS[name](
                question,
                ask_fn=lambda aq: user_sim.reply(vague, withheld, aq))
        else:
            retrieved, methods = CONFIGS[name](question)
        error = None
    except Exception as exc:                                  # noqa: BLE001
        retrieved, methods, error = [], None, repr(exc)
    ask = dict(run_eval.ASK_ROW) if asking else None
    return (retrieved, methods, error, ask,
            pipeline.take_ask_trace()["fallbacks"],
            pipeline.take_clarify_trace()["fallbacks"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vague", required=True, help="eval/vague_v1.json or "
                    "eval/uk/vague_v1.json")
    ap.add_argument("--gold", required=True, help="gold of the resolves_to needs "
                    "(au eval/gold_v8.json, UK eval/uk/gold_v2.json)")
    ap.add_argument("--condition", required=True, choices=CONDITIONS)
    ap.add_argument("--configs", default="c_routed,d_agent")
    ap.add_argument("--limit", type=int, default=0,
                    help="first N vague items only (smoke tests)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rewrite-log", default=None,
                    help="default: <out without .json>.rewrite.log")
    args = ap.parse_args()

    # ASK_MODE is the condition's, not the shell's.
    config.ASK_MODE = "on" if args.condition == "ask" else "off"
    os.environ["ASK_MODE"] = config.ASK_MODE

    with open(args.vague, encoding="utf-8") as f:
        vague = json.load(f)
    if vague.get("corpus") != config.CORPUS:
        raise SystemExit("%s is for corpus %r, this process is CORPUS=%s"
                         % (args.vague, vague.get("corpus"), config.CORPUS))
    with open(args.gold, encoding="utf-8") as f:
        spec = json.load(f)
    needs = {n["id"]: n for n in spec["needs"]}
    items = vague["items"][:args.limit] if args.limit else vague["items"]
    missing = [i["resolves_to"] for i in items if not needs.get(
        i["resolves_to"], {}).get("gold")]
    if missing:
        raise SystemExit("no gold for resolves_to: %s" % ", ".join(missing))
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    for c in configs:
        if c not in CONFIGS:
            raise SystemExit("unknown config %r" % c)

    print("condition %s | ASK_MODE=%s | ASK_STYLE=%s | USER_SIM=%s | %d items "
          "from %s | gold %s | configs %s"
          % (args.condition, config.ASK_MODE,
             config.ASK_STYLE if config.ASK_MODE == "on" else "-",
             config.USER_SIM, len(items), args.vague, args.gold, configs))
    if args.condition == "ask" and "c_routed" in configs:
        print("note: c_routed has no decide() step and cannot ask; under "
              "'ask' it runs as under 'noask'")

    # EVAL_RECORD_COST (default off, evaluation side only; see
    # eval/cost_record.py), as in run_eval.py.
    cost_rec = None
    if os.environ.get("EVAL_RECORD_COST", "0").lower() in ("1", "on", "true", "yes"):
        import cost_record
        cost_rec = cost_record.install({"pipeline": pipeline, "retrieval": retrieval,
                                        "rerank": rerank, "rewrite": rewrite, "hyde": hyde})
        print("EVAL_RECORD_COST=1: records[].cost holds LLM calls, tokens and stage seconds")

    records, rewrite_rows = [], []
    for item in items:
        need = needs[item["resolves_to"]]
        gold = set(need["gold"])
        groups = need.get("gold_groups") or [[i] for i in need["gold"]]
        question = question_for(args.condition, item)
        for name in configs:
            started = time.time()
            if cost_rec is not None:
                cost_rec.begin_row()
            retrieved, methods, error, ask, fallbacks, cfallbacks = run_row(
                name, args.condition, item, question)
            trace = rewrite.take_trace()
            p, r = routing_scores(methods, need.get("expect_methods"))
            rec = {
                "need": item["id"],
                "resolves_to": item["resolves_to"],
                "category": "vague",
                "level": "L1",
                "config": name,
                "condition": args.condition,
                "expansion_mode": config.EXPANSION_MODE,
                "question": question,
                "withheld_dim": item["withheld_dim"],
                "gold_size": len(gold),
                "gold_groups": len(groups),
                "retrieved": retrieved,
                "hits": sorted(set(retrieved) & gold),
                "recall@10": recall_at_k(retrieved, groups),
                "recall@10_norm": recall_norm(retrieved, groups),
                "mrr@10": mrr_at_k(retrieved, groups),
                "route_methods": methods,
                "route_precision": p,
                "route_recall": r,
                "iterations": ITERATIONS["count"],
                "rerank": rerank.take_trace(),
                **({"hyde": hyde.take_trace()} if hyde.active() else {}),
                "seconds": max(0.0, round(time.time() - started, 1)),
                "error": error,
            }
            if cost_rec is not None:
                rec["cost"] = cost_rec.end_row()
            if getattr(config, "HYDRATE_CANDIDATES", False):
                rec["hydration"] = retrieval.take_hydration_trace()
            if ask is not None:
                rec.update(ask)
                rec["ask_fallbacks"] = fallbacks
            elif config.ASK_MODE == "on":
                rec.update(run_eval._ask_row_blank())
                rec["ask_fallbacks"] = fallbacks
            if config.ASK_MODE == "on":
                rec["ask_style"] = config.ASK_STYLE
                rec["clarify_fallbacks"] = cfallbacks
            # USER_SIM=choice: what was offered and what the
            # simulated user picked (user_sim.CHOICE_TRACE). Only then.
            if config.USER_SIM == "choice" and ask is not None:
                choice = user_sim.take_choice_trace()
                rec["options"] = choice.get("options", rec.get("ask_options", []))
                rec["matched_option"] = choice.get("matched_option")
                rec["chosen_value"] = choice.get("chosen_value")
                rec["chosen_rank"] = choice.get("chosen_rank")
            records.append(rec)
            for entry in trace:
                rewrite_rows.append("\t".join([
                    item["id"], args.condition, name,
                    entry["question"].replace("\t", " "),
                    entry["rewritten"].replace("\t", " "), entry["status"],
                    entry.get("gate_reason", "")]))
            extra = ""
            if ask is not None and "clarify_raw" in ask:
                extra += "  clarify: %s" % " ".join(
                    (ask["clarify_raw"] or "").split())[:160]
            if ask is not None and ask.get("asked"):
                extra += "  asked: %r -> %r%s" % (
                    ask["ask_question"][:80], ask["ask_reply"],
                    "  [restart]" if ask.get("q_prime") else "")
            if error:
                print("  %-22s %-9s ERROR %s" % (item["id"], name, error[:90]))
            else:
                print("  %-22s %-9s norm=%.2f mrr=%.2f rounds=%d "
                      "(%.1fs)%s" % (item["id"], name, rec["recall@10_norm"],
                                     rec["mrr@10"],
                                     rec["iterations"], rec["seconds"], extra))

    print("\n=== means (%s) ===" % args.condition)
    for name in configs:
        rows = [r for r in records if r["config"] == name and not r["error"]]
        if not rows:
            continue
        print("  %-9s norm=%.3f MRR=%.3f n=%d%s"
              % (name, run_eval.mean([r["recall@10_norm"] for r in rows]),
                 run_eval.mean([r["mrr@10"] for r in rows]), len(rows),
                 ("  asked %d" % sum(1 for r in rows if r.get("asked")))
                 if config.ASK_MODE == "on" else ""))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"condition": args.condition,
                   "ask_mode": config.ASK_MODE,
                   "ask_max": config.ASK_MAX,
                   "ask_style": (config.ASK_STYLE if config.ASK_MODE == "on"
                                 else None),
                   "clarify_prompt_sha256": pipeline.CLARIFY_PROMPT_SHA256,
                   "user_sim": config.USER_SIM,
                   "ask_prompt_sha256": pipeline.ASK_PROMPT_SHA256,
                   "vague_file": args.vague,
                   "vague_sha256": sha256_file(args.vague),
                   "vague_rule_commit": vague.get("rule_commit"),
                   "gold_file": args.gold,
                   "corpus_fingerprint": spec["corpus_fingerprint"],
                   "seed": getattr(config, "LLM_SEED", None),
                   "expansion_mode": config.EXPANSION_MODE,
                   "index_name": config.INDEX_NAME,
                   "rerank": rerank.fingerprint(),
                   "limit": args.limit,
                   **({"eval_record_cost": True} if cost_rec is not None else {}),
                   "records": records}, f, ensure_ascii=False, indent=2)
    print("\nwrote %s" % args.out)
    log = args.rewrite_log or (os.path.splitext(args.out)[0] + ".rewrite.log")
    if rewrite_rows:
        with open(log, "w", encoding="utf-8") as f:
            f.write("need\tcondition\tconfig\tquestion\trewritten\tstatus\t"
                    "gate_reason\n")
            f.write("\n".join(rewrite_rows) + "\n")
        print("wrote %s" % log)


if __name__ == "__main__":
    main()
