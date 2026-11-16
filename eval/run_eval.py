"""Run every sub-question of every topic through system configurations and
score them.

Usage (inside a corpus environment, see eval/run_clear.sh)
-----
    python eval/run_eval.py --gold runs/au/topics.json \\
        --configs a_keyword,b_fused,c_routed,d_agent --out runs/au/results.json

--gold is the file eval/benchmark_input.py writes from benchmark/.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import config
import hyde
import retrieval
import pipeline
import rerank
import rewrite
import user_sim
from corpus_paths import eval_path  # noqa: E402


ITERATIONS = {"count": 1}


# Every configuration ends the same way -- retrieval.finalise() applies the
# reranker if one is active and then cuts to TOP_K -- so a reranker can never
# be applied to some configurations and not others.
def run_a_keyword(question):
    docs = retrieval.keyword_search(question, size=config.TOP_K)
    docs = retrieval.dedup_by_title(docs)
    return [d["id"] for d in retrieval.finalise(question, docs)], None


def run_b_fused(question):
    lists = {
        "keyword": retrieval.keyword_search(question, size=config.TOP_K * 2),
        "semantic": retrieval.semantic_search(question, size=config.TOP_K * 2),
    }
    fused = retrieval.rrf_fuse(lists)
    return [d["id"] for d in retrieval.finalise(question, fused)], None


# EVAL_GENERATE / EVAL_RECORD_POOL. What the last c_routed / d_agent row generated and pooled.
# Filled only under EVAL_GENERATE / EVAL_RECORD_POOL, written only then.
GEN_ROW = {}


def _gen_blank():
    return {"answer": None, "references": None, "validations": [],
            "regenerations": 0}


def _gen_take(acc, stage, payload):
    """Fold one answer_stage() item into acc. Returns True if it was one."""
    if stage == "answer":
        acc["answer"] = payload
    elif stage == "references":
        acc["references"] = json.loads(payload)
    elif stage == "validation":
        if payload.startswith("regeneration attempt"):
            acc["regenerations"] += 1
        else:
            acc["validations"].append(payload)
    else:
        return False
    return True


def _gen_fields(acc):
    """The record fields of one generated answer. Citations are resolved
    against the ten documents the generator saw (the references)."""
    answer = acc["answer"] or ""
    refused = answer == pipeline.REFUSAL
    cited, collisions, unresolved = ([], [], []) if refused else \
        pipeline.resolve_citations(answer, acc["references"] or [])
    last = acc["validations"][-1] if acc["validations"] else ""
    return {"answer": acc["answer"], "cited_ids": cited,
            "cite_collisions": collisions, "cite_unresolved": unresolved,
            "refused": refused,
            "validated": last == "grounded=True relevant=True",
            "regenerations": acc["regenerations"]}


def run_c_routed(question):
    plan = pipeline.route(question)
    rank_lists = pipeline.retrieve(question, plan)
    fused = retrieval.rrf_fuse(rank_lists)
    # EVAL_RECORD_POOL_METHODS: snapshot of the merged pool as it
    # goes into finalise(), taken before finalise() runs. Recording only.
    pool_methods = ({d["id"]: list(d.get("from_methods") or []) for d in fused}
                    if getattr(config, "EVAL_RECORD_POOL_METHODS", False) else None)
    top = retrieval.finalise(question, fused)
    # EVAL_RECORD_POOL / EVAL_GENERATE. Off, nothing below runs.
    if getattr(config, "EVAL_RECORD_POOL", False):
        GEN_ROW["pool_ids"] = [d["id"] for d in fused]
        GEN_ROW["round_methods"] = [list(plan.get("methods") or [])]
    if pool_methods is not None:
        GEN_ROW["pool_from_methods"] = pool_methods
    if getattr(config, "EVAL_GENERATE", False):
        # The same generation code d_agent runs, on the same inputs it would
        # take from this plan: question, top ten, place, intent, entity.
        acc = _gen_blank()
        for stage, payload in pipeline.answer_stage(
                question, top, plan.get("place", ""),
                plan.get("graph_intent", ""), plan.get("entity", "")):
            _gen_take(acc, stage, payload)
        GEN_ROW.update(_gen_fields(acc))
    return ([d["id"] for d in top],
            plan.get("methods") or [])


# What the last d_agent row did with the ask branch. Filled only when an
# ask_fn is passed (ASK_MODE=on), and written to the record only then.
ASK_ROW = {}


def _ask_row_blank():
    return {"asked": False, "ask_question": None, "ask_reply": None,
            "informative": None, "q_prime": None, "pre_ask_rounds": None}


def run_d_agent(question, ask_fn=None):
    ids, plan_methods, rounds = [], None, 1
    ask = _ask_row_blank()
    # Without ask_fn, run() is called without one.
    stream = (pipeline.run(question) if ask_fn is None
              else pipeline.run(question, ask_fn=ask_fn))
    gen = _gen_blank()
    for stage, payload in stream:
        if _gen_take(gen, stage, payload):
            continue
        if stage == "routing" and payload.startswith("{"):
            try:
                plan_methods = json.loads(payload).get("methods") or []
            except ValueError:
                pass
        elif stage == "reasoning" and payload.startswith("needs more evidence"):
            rounds += 1
        elif stage == "evidence_ids":
            ids = json.loads(payload)
        elif stage == "clarify":
            # The clarification step's raw verdict.
            ask["clarify_raw"] = payload
            # The grounded rule's inputs, parsed, so the
            # analysis needs no second parser. Only under grounded.
            if getattr(config, "ASK_STYLE", "") == "grounded":
                ask["clarify_rule"] = json.loads(payload)
        elif stage == "ask":
            ask["asked"] = True
            ask["ask_question"] = payload
            if getattr(config, "ASK_STYLE", "") == "grounded":
                ask["ask_options"] = list(getattr(payload, "options", None) or [])
        elif stage == "ask_reply":
            ask["ask_reply"] = payload
            ask["informative"] = pipeline.informative(payload)
        elif stage == "ask_restart":
            # The run starts again on q'; everything counted so far belongs
            # to the abandoned first attempt.
            ask["q_prime"] = payload
            ask["pre_ask_rounds"] = rounds
            ids, plan_methods, rounds = [], None, 1
    if ask_fn is not None:
        ASK_ROW.clear()
        ASK_ROW.update(ask)
    if getattr(config, "EVAL_GENERATE", False):
        GEN_ROW.update(_gen_fields(gen))
    if getattr(config, "EVAL_RECORD_POOL", False):
        GEN_ROW.update(pipeline.take_pool_trace())
    ITERATIONS["count"] = rounds
    if not ids:
        raise RuntimeError("pipeline.run() yielded no evidence_ids")
    return ids[:config.TOP_K], plan_methods


CONFIGS = {
    "a_keyword": run_a_keyword,
    "b_fused": run_b_fused,
    "c_routed": run_c_routed,
    "d_agent": run_d_agent,
}


def recall_at_k(retrieved, groups):
    if not groups:
        return None
    found = set(retrieved)
    hit = sum(1 for g in groups if found & set(g))
    return hit / float(len(groups))


def recall_norm(retrieved, groups):
    if not groups:
        return None
    found = set(retrieved)
    hit = sum(1 for g in groups if found & set(g))
    return hit / float(min(10, len(groups)))


def mrr_at_k(retrieved, groups):
    if not groups:
        return None
    gold = {i for g in groups for i in g}
    for rank, eid in enumerate(retrieved, start=1):
        if eid in gold:
            return 1.0 / rank
    return 0.0


def routing_scores(chosen, expected):
    if chosen is None or not expected:
        return None, None
    chosen, expected = set(chosen), set(expected)
    hit = len(chosen & expected)
    precision = hit / float(len(chosen)) if chosen else 0.0
    recall = hit / float(len(expected))
    return precision, recall


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else float("nan")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default=eval_path("gold.json"))
    parser.add_argument("--out", default=eval_path("results.json"))
    parser.add_argument("--configs", default="a_keyword,c_routed")
    parser.add_argument("--levels", default="L1,L2,L3,L4")
    parser.add_argument("--rewrite-log", default=None,
                        help="Where to write the per-question rewrite log. "
                             "Defaults to eval/rewrite_<mode>_terms.log under "
                             "c1 / c2, and to nothing under the other modes. "
                             "Pass an explicit path when running a subset of "
                             "configs, or a partial log overwrites a full one.")
    args = parser.parse_args()

    rewriting = config.EXPANSION_MODE in ("c1", "c2")
    rewrite_log = args.rewrite_log
    if rewriting and rewrite_log is None:
        # config.EVAL_DIR is "eval" for au, eval/<name> for other corpora.
        rewrite_log = os.path.join(config.EVAL_DIR, "rewrite_%s_terms.log"
                                   % config.EXPANSION_MODE)
    rewrite_rows = []

    spec = json.load(open(args.gold))
    needs = spec["needs"]

    wanted_configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    wanted_levels = [l.strip() for l in args.levels.split(",") if l.strip()]

    print("corpus fingerprint %s, %d elements"
          % (spec["corpus_fingerprint"], spec["corpus_size"]))
    print("temperature %.1f / regen %.1f, seed %s"
          % (getattr(config, "LLM_TEMPERATURE", float("nan")),
             getattr(config, "LLM_REGEN_TEMPERATURE", float("nan")),
             getattr(config, "LLM_SEED", "unset")))
    print("expansion mode: %s" % config.EXPANSION_MODE)
    print("index: %s | bm25 msm: %r | bge query prefix: %s"
          % (config.INDEX_NAME,
             getattr(config, "BM25_MIN_SHOULD_MATCH", ""),
             getattr(config, "EMBED_QUERY_PREFIX", False)))
    print("candidate pool: %d | title dedup: %s | en subfields: %s | rrf_k: %d"
          % (retrieval.candidate_pool(),
             getattr(config, "DEDUP_BY_TITLE", False),
             getattr(config, "USE_EN_SUBFIELDS", False),
             config.RRF_K))
    print("rerank: %s" % json.dumps(rerank.fingerprint(), sort_keys=True))
    if getattr(config, "HYDRATE_CANDIDATES", False):
        print("candidate hydration: on")
    print("spatial topic gate: %s"
          % ("on" if config.SPATIAL_TOPIC_GATE else "off"))
    print("fusion candidate depth: %d%s"
          % (config.FUSION_DEPTH,
             "" if config.FUSION_DEPTH == config.TOP_K else "  [not TOP_K]"))
    if rewriting:
        print("rewrite gate: %s%s"
              % (config.REWRITE_GATE,
                 "  [gate on]" if config.REWRITE_GATE == "question_type"
                 else ""))
        print("rewrite guard: %s%s"
              % (config.REWRITE_GUARD,
                 "  [token guard]" if config.REWRITE_GUARD == "token" else ""))
        print("rewrite prompt sha256: %s" % rewrite.PROMPT_SHA256)
        print("rewrite log: %s" % rewrite_log)
    if getattr(config, "EVAL_GENERATE", False) or \
            getattr(config, "EVAL_RECORD_POOL", False):
        print("EVAL_GENERATE=%d EVAL_RECORD_POOL=%d"
              % (bool(config.EVAL_GENERATE), bool(config.EVAL_RECORD_POOL)))
    if getattr(config, "EVAL_RECORD_POOL_METHODS", False):
        print("EVAL_RECORD_POOL_METHODS=1: c_routed rows record pool_from_methods")
    if getattr(config, "ASK_MODE", "off") == "on":
        print("ask mode: on, ASK_STYLE=%s, ASK_MAX=%d, USER_SIM=%s, "
              "prompt sha256 %s"
              % (config.ASK_STYLE, config.ASK_MAX, config.USER_SIM,
                 pipeline.CLARIFY_PROMPT_SHA256
                 if config.ASK_STYLE == "separate"
                 else pipeline.ASK_PROMPT_SHA256))
    print("configs: %s | levels: %s\n" % (wanted_configs, wanted_levels))

    # EVAL_RECORD_COST (default off, evaluation side only; see
    # eval/cost_record.py).
    cost_rec = None
    if os.environ.get("EVAL_RECORD_COST", "0").lower() in ("1", "on", "true", "yes"):
        import cost_record
        cost_rec = cost_record.install({"pipeline": pipeline, "retrieval": retrieval,
                                        "rerank": rerank, "rewrite": rewrite, "hyde": hyde})
        print("EVAL_RECORD_COST=1: records[].cost holds LLM calls, tokens and stage seconds\n")

    records = []
    for need in needs:
        gold = set(need["gold"])
        groups = need.get("gold_groups") or ([[i] for i in need["gold"]])
        for level in wanted_levels:
            question = need["phrasings"].get(level)
            if not question:
                continue
            for name in wanted_configs:
                started = time.time()
                ITERATIONS["count"] = 1
                rewrite.reset_trace()
                rerank.reset_trace()
                retrieval.reset_hydration_trace()
                hyde.reset_trace()
                gen_on = getattr(config, "EVAL_GENERATE", False) or \
                    getattr(config, "EVAL_RECORD_POOL", False) or \
                    getattr(config, "EVAL_RECORD_POOL_METHODS", False)
                if gen_on:
                    GEN_ROW.clear()
                    pipeline.reset_pool_trace()
                ask_on = getattr(config, "ASK_MODE", "off") == "on"
                if ask_on:
                    ASK_ROW.clear()
                    ASK_ROW.update(_ask_row_blank())
                    pipeline.reset_ask_trace()
                    pipeline.reset_clarify_trace()
                if cost_rec is not None:
                    cost_rec.begin_row()
                # Publisher linking: c_routed calls retrieve() without run(), so
                # the resolution trace is reset here for every row.
                if getattr(config, "ENTITY_RESOLVE", "off") != "off":
                    pipeline.reset_publisher_trace()
                try:
                    if ask_on and name == "d_agent":
                        # A topic's sub-question withholds nothing, so the
                        # template simulator always gives the null reply.
                        retrieved, methods = CONFIGS[name](
                            question, ask_fn=lambda aq, q=question:
                            user_sim.reply(q, None, aq))
                    else:
                        retrieved, methods = CONFIGS[name](question)
                    error = None
                except Exception as exc:            # noqa: BLE001
                    retrieved, methods, error = [], None, repr(exc)
                trace = rewrite.take_trace()
                p, r = routing_scores(methods, need.get("expect_methods"))
                records.append({
                    "need": need["id"],
                    "category": need["category"],
                    "level": level,
                    "config": name,
                    "expansion_mode": config.EXPANSION_MODE,
                    "question": question,
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
                })
                if cost_rec is not None:
                    records[-1]["cost"] = cost_rec.end_row()
                # Publisher linking. Only with the switch on: what each retrieve()
                # call of the row (d_agent: of its final run) resolved.
                if getattr(config, "ENTITY_RESOLVE", "off") != "off":
                    records[-1]["entity_resolution"] = pipeline.take_publisher_trace()
                # Written only when HYDRATE_CANDIDATES is on.
                if getattr(config, "HYDRATE_CANDIDATES", False):
                    records[-1]["hydration"] = retrieval.take_hydration_trace()
                # Only with EVAL_GENERATE / EVAL_RECORD_POOL on,
                # and only on the two configurations that produce them.
                if gen_on and name in ("c_routed", "d_agent") and not error:
                    records[-1].update(dict(GEN_ROW))
                # Only with ASK_MODE=on, for the same reason.
                if ask_on:
                    records[-1].update(dict(ASK_ROW))
                    records[-1]["ask_fallbacks"] = \
                        pipeline.take_ask_trace()["fallbacks"]
                    records[-1]["ask_style"] = config.ASK_STYLE
                    records[-1]["clarify_fallbacks"] = \
                        pipeline.take_clarify_trace()["fallbacks"]
                for entry in trace:
                    # need | level | config | original | rewritten | status |
                    # why the gate opened | tokens dropped by the df==0 guard
                    # | identity tokens the guard required | R@10
                    rewrite_rows.append("\t".join([
                        need["id"], level, name,
                        entry["question"].replace("\t", " "),
                        entry["rewritten"].replace("\t", " "),
                        entry["status"],
                        entry.get("gate_reason", ""),
                        ",".join(entry["dropped"]),
                        ",".join(entry.get("required")
                                 or entry.get("missing") or []),
                        "%.3f" % records[-1]["recall@10"]
                        if records[-1]["recall@10"] is not None else "n/a",
                    ]))

                if error:
                    print("  %-14s %-3s %-10s ERROR %s"
                          % (need["id"], level, name, error[:90]))
                elif records[-1]["recall@10"] is None:
                    print("  %-14s %-3s %-10s R@10=n/a  (%.1fs)"
                          % (need["id"], level, name, records[-1]["seconds"]))
                else:
                    rounds = ""
                    if name == "d_agent" and records[-1]["iterations"] > 1:
                        rounds = "  rounds=%d" % records[-1]["iterations"]
                    print("  %-14s %-3s %-10s R@10=%.2f  (%.1fs)%s"
                          % (need["id"], level, name,
                             records[-1]["recall@10"],
                             records[-1]["seconds"], rounds))

    print("\n=== Recall@10, per need, averaged over phrasings ===")
    header = "%-14s %-10s %6s" % ("need", "category", "groups")
    for name in wanted_configs:
        header += " %10s" % name
    print(header)
    for need in needs:
        if not need["gold"]:
            continue
        groups = need.get("gold_groups") or [[i] for i in need["gold"]]
        line = "%-14s %-10s %6d" % (need["id"], need["category"], len(groups))
        for name in wanted_configs:
            vals = [r["recall@10"] for r in records
                    if r["need"] == need["id"] and r["config"] == name]
            line += " %10.2f" % mean(vals)
        print(line)

    # Topics marked score_scope=diagnostic are held out of every mean. The
    # topics in benchmark/ carry no such field, so this is empty for them.
    diagnostic = {n["id"] for n in needs
                  if n.get("score_scope") == "diagnostic"}
    main_records = [r for r in records if r["need"] not in diagnostic]
    held_records = [r for r in records if r["need"] in diagnostic]
    if diagnostic:
        print("\ndiagnostic needs (score_scope), excluded from every mean "
              "below: %s" % ", ".join(sorted(diagnostic)))

    print("\n=== means over all needs and phrasings ===")
    for name in wanted_configs:
        subset = [r for r in main_records if r["config"] == name]
        print("  %-12s norm=%.3f  raw R@10=%.3f  MRR@10=%.3f  n=%d"
              % (name,
                 mean([r["recall@10_norm"] for r in subset]),
                 mean([r["recall@10"] for r in subset]),
                 mean([r["mrr@10"] for r in subset]),
                 len(subset)))
        route_p = mean([r["route_precision"] for r in subset])
        if route_p == route_p:
            print("               routing precision=%.3f recall=%.3f"
                  % (route_p, mean([r["route_recall"] for r in subset])))
        # How much reordering the reranker actually did, rather than an
        # assertion that it did any.
        fired = [r["rerank"] for r in subset
                 if r.get("rerank") and r["rerank"].get("calls")]
        if fired:
            print("               rerank: fired on %d of %d rows, mean pool "
                  "%.1f, mean %.2f documents moved into the top ten"
                  % (len(fired), len(subset),
                     sum(x["docs"] for x in fired) / float(len(fired)),
                     sum(x["moved_into_top_k"] for x in fired)
                     / float(len(fired))))
        if name == "d_agent" and subset:
            loops = [r["iterations"] for r in subset]
            extra = [r for r in subset if r["iterations"] > 1]
            print("               retrieval rounds: mean=%.2f, "
                  "more than one on %d of %d"
                  % (sum(loops) / float(len(loops)), len(extra), len(loops)))
            if extra:
                print("               looped on: %s"
                      % ", ".join("%s/%s" % (r["need"], r["level"])
                                  for r in extra))
            if getattr(config, "ASK_MODE", "off") == "on":
                asked = [r for r in subset if r.get("asked")]
                print("               asked (%s): %d of %d, informative "
                      "reply on %d, ask fallbacks %d, clarify fallbacks %d"
                      % (config.ASK_STYLE, len(asked), len(subset),
                         sum(1 for r in asked if r.get("informative")),
                         sum(r.get("ask_fallbacks") or 0 for r in subset),
                         sum(r.get("clarify_fallbacks") or 0 for r in subset)))

    print("\n=== normalised recall by phrasing level (robustness) ===")
    for level in wanted_levels:
        line = "  %-4s" % level
        for name in wanted_configs:
            vals = [r["recall@10_norm"] for r in main_records
                    if r["level"] == level and r["config"] == name]
            line += "  %s=%.3f" % (name, mean(vals))
        print(line)

    if held_records:
        print("\n=== DIAGNOSTIC needs -- excluded from every mean above ===")
        print("  %d need(s): %s"
              % (len(diagnostic), ", ".join(sorted(diagnostic))))
        for name in wanted_configs:
            subset = [r for r in held_records if r["config"] == name]
            print("  %-12s norm=%.3f  raw R@10=%.3f  MRR@10=%.3f  n=%d"
                  % (name,
                     mean([r["recall@10_norm"] for r in subset]),
                     mean([r["recall@10"] for r in subset]),
                     mean([r["mrr@10"] for r in subset]),
                     len(subset)))
        for need_id in sorted(diagnostic):
            rows = [r for r in held_records if r["need"] == need_id]
            if not rows:
                continue
            line = "    %-14s groups=%-5d" % (need_id, rows[0]["gold_groups"])
            for name in wanted_configs:
                line += "  %s=%.3f" % (name, mean(
                    [r["recall@10_norm"] for r in rows
                     if r["config"] == name]))
            print(line)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"corpus_fingerprint": spec["corpus_fingerprint"],
                   "temperature": getattr(config, "LLM_TEMPERATURE", None),
                   "seed": getattr(config, "LLM_SEED", None),
                   "expansion_mode": config.EXPANSION_MODE,
                   "spatial_topic_gate": bool(config.SPATIAL_TOPIC_GATE),
                   "fusion_depth": config.FUSION_DEPTH,
                   "rewrite_guard": config.REWRITE_GUARD,
                   "rewrite_gate": config.REWRITE_GATE,
                   "index_name": config.INDEX_NAME,
                   "rrf_k": config.RRF_K,
                   "candidate_pool": retrieval.candidate_pool(),
                   "bm25_min_should_match": config.BM25_MIN_SHOULD_MATCH,
                   "embed_query_prefix": config.EMBED_QUERY_PREFIX,
                   "dedup_by_title": config.DEDUP_BY_TITLE,
                   "use_en_subfields": config.USE_EN_SUBFIELDS,
                   "rerank": rerank.fingerprint(),
                   **({"hyde": hyde.fingerprint()} if hyde.active() else {}),
                   **({"hydrate_candidates": True}
                      if getattr(config, "HYDRATE_CANDIDATES", False) else {}),
                   **({"eval_generate": True}
                      if getattr(config, "EVAL_GENERATE", False) else {}),
                   **({"eval_record_pool": True}
                      if getattr(config, "EVAL_RECORD_POOL", False) else {}),
                   **({"eval_record_cost": True} if cost_rec is not None else {}),
                   **({"eval_record_pool_methods": True}
                      if getattr(config, "EVAL_RECORD_POOL_METHODS", False) else {}),
                   **({"ask_mode": "on", "ask_max": config.ASK_MAX,
                       "user_sim": config.USER_SIM,
                       "ask_style": config.ASK_STYLE}
                      if getattr(config, "ASK_MODE", "off") == "on" else {}),
                   "records": records}, f, ensure_ascii=False, indent=2)
    print("\nwrote %s" % args.out)

    if rewrite_log and rewrite_rows:
        os.makedirs(os.path.dirname(rewrite_log) or ".", exist_ok=True)
        with open(rewrite_log, "w") as f:
            f.write("need\tlevel\tconfig\tquestion\trewritten\tstatus\t"
                    "gate_reason\tdropped_df0\tidentity_tokens\trecall@10\n")
            f.write("\n".join(rewrite_rows) + "\n")

        def count(marker):
            return sum(1 for row in rewrite_rows if "\t%s\t" % marker in row)

        skipped = count("skipped:canonical")
        kept = count("ok")
        vacuous = count("ok:no_identity_token")
        fell_back = len(rewrite_rows) - kept - vacuous - skipped
        print("wrote %s (%d calls: %d skipped by the gate, %d kept, %d kept "
              "with no identity token, %d fell back)"
              % (rewrite_log, len(rewrite_rows), skipped, kept, vacuous,
                 fell_back))


if __name__ == "__main__":
    main()
