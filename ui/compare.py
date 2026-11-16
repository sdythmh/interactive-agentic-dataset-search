"""The comparison view: the agent's final ten next to simpler searches."""
import copy

LABELS = {
    "keyword": "Keyword search",
    "single_round": "Single search round",
    "before_clarification": "Before your clarification",
    "agent": "Agent",
}
TEXT_PATHS = ("keyword", "semantic")


class Backend:
    """The system functions a column needs. ui/engine.py passes the
    unobserved originals; tests pass fakes."""

    def __init__(self, keyword_search, dedup_by_title, finalise, trace=None):
        self.keyword_search = keyword_search
        self.dedup_by_title = dedup_by_title
        self.finalise = finalise
        self.trace = trace


class PoolRecorder:
    """Collects what build() needs while pipeline.run() streams, from the
    observer records of ui/observe.py and the (stage, message) pairs.
    """

    def __init__(self, question):
        self.runs = [self._run(question)]
        self.final = None
        self.plans = []

    @staticmethod
    def _run(question):
        return {"question": question, "searched": None, "pool": None,
                "rounds": 0}

    def stage(self, stage, message):
        if stage == "retrieve":
            self.runs[-1]["rounds"] += 1
        elif stage == "ask_restart":
            self.runs.append(self._run(message))

    def record(self, kind, phase, payload):
        if phase != "end":
            return
        run = self.runs[-1]
        if kind == "memory":
            run["searched"] = payload.get("result")
        elif kind == "route":
            self.plans.append(payload.get("plan"))
        elif kind == "fuse" and "pool" in payload:
            inputs = payload.get("inputs") or {}
            across = bool(inputs) and all(str(k).startswith("round")
                                          for k in inputs)
            if not across and run["rounds"] == 1 and run["pool"] is None:
                run["pool"] = payload["pool"]
        elif kind == "finalise":
            self.final = payload.get("top")


def _call_preserving(trace, fn, *args, **kwargs):
    """Run fn and put the re-ranker trace back as it was, so a column never
    shows up in the job's own counters."""
    saved = copy.deepcopy(trace) if trace is not None else None
    try:
        return fn(*args, **kwargs)
    finally:
        if trace is not None:
            trace.clear()
            trace.update(saved)


def _rows(docs, top_k):
    return [{"id": d.get("id"), "rank": i + 1, "title": d.get("title", ""),
             "organization": d.get("organization", "")}
            for i, d in enumerate(list(docs)[:top_k])]


def _column(cid, question, rows=None, error=None):
    col = {"id": cid, "label": LABELS[cid], "question_used": question,
           "available": error is None, "shown": True,
           "records": rows if error is None else [], "reason": error}
    return col


def keyword_column(question, backend, top_k):
    """keyword_search -> dedup_by_title -> finalise, as eval/run_eval.py's
    run_a_keyword does it."""
    def run():
        docs = backend.keyword_search(question, size=top_k)
        docs = backend.dedup_by_title(docs)
        return backend.finalise(question, docs, top_k=top_k)
    top = _call_preserving(backend.trace, run)
    return _rows(top, top_k)


def pool_column(pool, question, backend, top_k):
    """A recorded candidate pool, re-ranked and cut as finalise() does."""
    docs = copy.deepcopy(list(pool))
    top = _call_preserving(backend.trace, backend.finalise, question, docs,
                           top_k=top_k)
    return _rows(top, top_k)


def _safe(fn, *args):
    try:
        return fn(*args), None
    except Exception as exc:                                   # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:300] or "failed")


def _rounds_of(doc):
    """Search rounds that put the document into the final candidate pool."""
    names = list((doc.get("round_ranks") or {}).keys())
    if not names:
        names = [m for m in (doc.get("from_methods") or [])
                 if str(m).startswith("round")]
    nums = sorted({int(str(n)[5:]) for n in names if str(n)[5:].isdigit()})
    return nums or [1]


def _paths_of(doc):
    ranks = doc.get("path_ranks")
    if ranks:
        return [p for p in ("keyword", "semantic", "spatial", "graph", "publisher")
                if p in ranks]
    return [m for m in (doc.get("from_methods") or [])
            if not str(m).startswith("round")]


def _rank_in(col, doc_id):
    if not col or not col["available"]:
        return None
    for r in col["records"]:
        if r["id"] == doc_id:
            return r["rank"]
    return None


def _changed(agent_ids, col):
    if not col or not col["available"]:
        return None
    other = {r["id"] for r in col["records"]}
    return sum(1 for i in agent_ids if i not in other)


def build(runs, final_docs, backend, top_k=10):
    """The data of one comparison event."""
    last = runs[-1]
    restarted = len(runs) > 1

    columns = []
    rows, err = _safe(keyword_column, last.get("searched") or last["question"],
                      backend, top_k)
    keyword = _column("keyword", last.get("searched") or last["question"],
                      rows, err)
    columns.append(keyword)

    if last.get("pool") is None:
        single = _column("single_round", last["question"],
                         error="The first-round candidates were not recorded.")
    else:
        rows, err = _safe(pool_column, last["pool"], last["question"],
                          backend, top_k)
        single = _column("single_round", last["question"], rows, err)
    columns.append(single)

    before = None
    if restarted:
        first = runs[0]
        if first.get("pool") is None:
            before = _column("before_clarification", first["question"],
                             error="The first-round candidates were not recorded.")
        else:
            rows, err = _safe(pool_column, first["pool"], first["question"],
                              backend, top_k)
            before = _column("before_clarification", first["question"], rows, err)
        columns.append(before)

    agent_rows = _rows(final_docs, top_k)
    agent = _column("agent", last["question"], agent_rows)
    columns.append(agent)
    agent_ids = [r["id"] for r in agent_rows]

    rounds = int(last.get("rounds") or 1)
    same = bool(rounds == 1 and single["available"]
                and [r["id"] for r in single["records"]] == agent_ids)
    # Shown unless it is the agent's list over again; a single-round run whose
    # re-ranked pool somehow differs keeps the column, so a difference is never
    # hidden.
    single["shown"] = not same

    first_pool = None
    if restarted and runs[0].get("pool") is not None:
        first_pool = {d.get("id") for d in runs[0]["pool"]}

    per_record = []
    for i, doc in enumerate(list(final_docs)[:top_k]):
        did = doc.get("id")
        ranks = {"keyword": _rank_in(keyword, did),
                 "single_round": _rank_in(single, did)}
        if restarted:
            ranks["before_clarification"] = _rank_in(before, did)
        per_record.append({
            "id": did, "rank": i + 1, "title": doc.get("title", ""),
            "organization": doc.get("organization", ""),
            "ranks": ranks,
            "rounds": _rounds_of(doc),
            "paths": _paths_of(doc),
            "path_ranks": {k: int(v) + 1 for k, v in
                           (doc.get("path_ranks") or {}).items()},
            "round_ranks": {k: int(v) + 1 for k, v in
                            (doc.get("round_ranks") or {}).items()},
            "graph_relation": doc.get("graph_relation") or None,
            "after_clarification": (None if first_pool is None
                                    else did not in first_pool),
        })

    summary = {"rounds": rounds, "restarted": restarted,
               "same_as_single_round": same,
               "changed_vs_single_round": _changed(agent_ids, single),
               "changed_vs_keyword": _changed(agent_ids, keyword)}
    if restarted:
        summary["changed_by_clarification"] = _changed(agent_ids, before)
    return {"columns": columns, "per_record": per_record, "summary": summary}


def unavailable(runs, final_docs, reason, top_k=10):
    """The event data when the comparison could not be built at all: every
    column but the agent's is not available, with the reason."""
    last = runs[-1] if runs else {"question": ""}
    columns = [_column("keyword", last.get("searched") or last.get("question", ""),
                       error=reason),
               _column("single_round", last.get("question", ""), error=reason)]
    if len(runs) > 1:
        columns.append(_column("before_clarification", runs[0].get("question", ""),
                               error=reason))
    agent_rows = _rows(final_docs, top_k)
    columns.append(_column("agent", last.get("question", ""), agent_rows))
    summary = {"rounds": int(last.get("rounds") or 1), "restarted": len(runs) > 1,
               "same_as_single_round": False, "changed_vs_single_round": None,
               "changed_vs_keyword": None}
    if len(runs) > 1:
        summary["changed_by_clarification"] = None
    per_record = [{"id": r["id"], "rank": r["rank"], "title": r["title"],
                   "organization": r["organization"],
                   "ranks": {c["id"]: None for c in columns if c["id"] != "agent"},
                   "rounds": [], "paths": [], "path_ranks": {}, "round_ranks": {},
                   "graph_relation": None, "after_clarification": None}
                  for r in agent_rows]
    return {"columns": columns, "per_record": per_record, "summary": summary}
