"""Runs one question through pipeline.run and turns what happens into events."""
import itertools
import json
import re
import threading
import time
import traceback

import config
import pipeline
import retrieval
import rerank
import rewrite

from ui import compare, observe, settings, vocab
from ui.records import Records, plain_text, short_text

KNOWN_PATHS = vocab.PATH_ORDER
HISTORY_WINDOW = 4          # earlier messages kept as conversation context


# ---- the job --------------------------------------------------------------
class Job:
    def __init__(self, job_id, corpus, question, history=None, ask=True,
                 emit=None, compare=False):
        self.id = job_id
        self.corpus = corpus
        self.question = question
        self.history = clean_history(history)
        self.ask = bool(ask)
        # The page asked for the comparison view; it is built only when the
        # worker also runs with it available (settings.UI_COMPARE).
        self.compare = bool(compare)
        self.emit = emit or (lambda event: None)
        self.cancelled = False
        self.awaiting_reply = False
        self.timed_out = False
        self._cv = threading.Condition()
        self._reply = None

    def submit_reply(self, text):
        """False when no clarifying question is waiting."""
        with self._cv:
            if not self.awaiting_reply or self._reply is not None:
                return False
            self._reply = text
            self._cv.notify_all()
            return True

    def cancel(self):
        with self._cv:
            self.cancelled = True
            self._cv.notify_all()

    def expect_reply(self):
        """Called when the question is announced, before the pipeline blocks,
        so a reply that arrives at once is accepted."""
        with self._cv:
            self._reply = None
            self.awaiting_reply = True

    def wait_reply(self, timeout):
        """The reply text, or None on timeout / cancellation."""
        deadline = time.monotonic() + timeout
        with self._cv:
            self.awaiting_reply = True
            try:
                while self._reply is None and not self.cancelled:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return None
                    self._cv.wait(min(left, 1.0))
                return self._reply
            finally:
                self.awaiting_reply = False


def clean_history(history):
    out = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role, content = item.get("role"), item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    return out[-HISTORY_WINDOW:]


# ---- plain wording --------------------------------------------------------
_REWRITE_OUTCOMES = {
    "ok": "Rewritten to match the catalogue's wording.",
    "ok:no_identity_token": "Rewritten to match the catalogue's wording.",
    "skipped:canonical": "Your question already matches the catalogue's wording.",
    "fallback:must_keep": "The rewrite lost the place or name you mentioned, so your wording was kept.",
    "fallback:empty": "The rewrite came back empty, so your wording was kept.",
    "fallback:empty_after_grounding": "The rewrite used words the catalogue does not contain, so your wording was kept.",
    "fallback:llm_error": "The rewriting step failed, so your wording was kept.",
}

# Why a produced rewrite was not used, as the end of "... not used, because X".
_REJECTED_REASONS = {
    "fallback:must_keep": "it lost the place or name in the question",
    "fallback:empty_after_grounding":
        "it used words the catalogue does not contain",
}

_NOTICES = (
    (re.compile(r"^\[warn\] spatial: no bbox for place '?\"?(.*?)'?\"? \(normalised"),
     "No area could be found for \"{0}\", so spatial search returned nothing."),
    (re.compile(r"^\[warn\] spatial: .* matched no documents"),
     "Spatial search found no datasets in that area."),
    (re.compile(r"^\[info\] spatial: corpus .* provides no place resolution"),
     "This catalogue has no place list, so spatial search was skipped."),
    (re.compile(r"^\[warn\] graph: no anchor found for '?\"?(.*?)'?\"?$"),
     "The knowledge graph has no dataset called \"{0}\", so graph search found nothing."),
    (re.compile(r"^\[warn\] graph: anchor .* has no \w+ edges"),
     "The matching dataset has no recorded relations in the knowledge graph."),
    (re.compile(r"^\[warn\] graph: .* returned nothing"),
     "Knowledge graph search returned nothing for that request."),
    (re.compile(r"^\[warn\] empty LLM response"),
     "The language model returned an empty reply."),
    (re.compile(r"^\[warn\] rewrite: LLM call failed"),
     "The rewriting step failed, so your wording was kept."),
)


def notice_for(line):
    """(text, detail) for a log line worth showing, else None."""
    line = line.strip()
    for pattern, template in _NOTICES:
        m = pattern.match(line)
        if m:
            return template.format(*m.groups()), line
    if line.startswith("[warn]"):
        return "A warning was logged during this step.", line
    return None


def describe_error(exc):
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    text = "%s: %s" % (name, exc)
    if isinstance(exc, SystemExit):
        return "config", ("The search could not start because of a "
                          "configuration problem."), text
    if module.startswith("opensearchpy") or "opensearch" in module:
        return "index", "The search index could not be reached.", text
    if module.startswith("neo4j"):
        return "graph", "The knowledge graph database could not be reached.", text
    if module.startswith("openai") or module.startswith("httpx"):
        return "llm", "The language model could not be reached.", text
    return "internal", "Something went wrong while searching.", text


def _path_names(doc):
    """The retrieval paths that returned a document, in display order."""
    ranks = doc.get("path_ranks") or {}
    names = [p for p in KNOWN_PATHS if p in ranks]
    if not names:
        names = [p for p in KNOWN_PATHS if p in (doc.get("from_methods") or [])]
    return names


def _one_based(ranks):
    return {k: int(v) + 1 for k, v in (ranks or {}).items() if k in KNOWN_PATHS}


def _path_chip(path):
    return {"id": path, "label": vocab.path_label(path),
            "color": vocab.PATH_COLORS.get(path)}


# ---- the sink: observer records + stage stream -> events ------------------
class _JobSink:
    def __init__(self, engine, job):
        self.engine = engine
        self.job = job
        self.t0 = time.monotonic()
        self.seq = 0
        self.run_index = 0
        self.round = 0
        self.attempts = 0
        self.steps = 0
        self._step_ids = itertools.count(1)
        self._open = {}                 # key -> (step id, started)
        self._path_query = {}
        self._rerank_rows = {}          # id -> (rank_before, rank_after)
        self._rerank_before = []
        self.final_docs = []
        self._cite_mark = 0
        self.answer_passed = False
        self.errors = 0
        # The comparison view. capture_pools is read by ui/observe.py, which
        # then adds deep copies of each fused pool to its records; the recorder
        # keeps what ui/compare.py needs.
        self.capture_pools = False
        self.recorder = None

    # -- emitting --
    def elapsed(self):
        return round(time.monotonic() - self.t0, 3)

    def emit(self, type_, **fields):
        self.seq += 1
        event = {"seq": self.seq, "t": self.elapsed(), "type": type_,
                 "run": self.run_index}
        event.update(fields)
        self.job.emit(event)
        return event

    def step_start(self, key, kind, label, **extra):
        step_id = "s%d" % next(self._step_ids)
        self._open[key] = (step_id, time.monotonic())
        self.emit("step_start", id=step_id, kind=kind, label=label, **extra)
        return step_id

    def step_end(self, key, summary=""):
        opened = self._open.pop(key, None)
        if opened is None:
            return
        step_id, began = opened
        self.steps += 1
        self.emit("step_end", id=step_id,
                  duration_ms=int((time.monotonic() - began) * 1000),
                  summary=summary)

    # -- observer records --
    def record(self, kind, phase, p):
        if self.recorder is not None:
            self.recorder.record(kind, phase, p)
        handler = getattr(self, "_%s_%s" % (kind, phase), None)
        if handler is not None:
            handler(p)

    # memory
    def _memory_start(self, p):
        if p.get("history_len"):
            self.step_start("memory", "understand",
                            "Understanding your question in context…")

    def _memory_end(self, p):
        question, result = p["question"], p["result"]
        self.emit("understanding", original=question,
                  contextual=result if result != question else None,
                  messages_used=min(p.get("history_len", 0), 2))
        self.step_end("memory", "Understood in context" if result != question
                      else "")

    # route
    def _route_start(self, p):
        self.step_start("route", "plan", "Planning the search…",
                        round=self.round + 1)

    def _route_end(self, p):
        plan = p["plan"] or {}
        methods = [m for m in vocab.PATH_ORDER if m in (plan.get("methods") or [])]
        place = plan.get("place") or ""
        intent = plan.get("graph_intent") or ""
        entity = plan.get("entity") or ""
        skipped = []
        if "spatial" in methods and not place:
            skipped.append({"id": "spatial",
                            "reason": "No place was found in the question."})
        if "graph" in methods and not intent:
            skipped.append({"id": "graph",
                            "reason": "No relation between datasets was found in the question."})
        area = None
        if place:
            try:
                area = Records.query_area(place)
            except Exception:                                  # noqa: BLE001
                area = None
        # Publisher linking: what the publisher list makes of this plan,
        # computed by the same read-only resolve_publisher() that retrieve()
        # calls next with the same question.
        extra = {}
        mode = getattr(config, "ENTITY_RESOLVE", "off")
        if mode != "off":
            try:
                # link_publisher caches per (entity, question), so the
                # retrieve() call that follows gets this very result.
                if mode.startswith("link_"):
                    res = retrieval.link_publisher(entity, p.get("question") or "", plan)
                else:
                    res = retrieval.resolve_publisher(entity, p.get("question") or "")
                summary = {"status": res["status"], "source": res["source"],
                           "entity": res["entity"] or None,
                           "names": res["names"][:5], "datasets": len(res["ids"]),
                           "family_size": res["family_size"], "cap": res["cap"],
                           # whether the search is restricted to it (path modes)
                           "filters": mode in ("path", "both", "link_path", "link_both")}
                if res.get("source") == "link":
                    summary.update(
                        candidates=[{"name": c["name"], "datasets": c["datasets"],
                                     "chosen": bool(c.get("chosen"))}
                                    for c in res["candidates"]],
                        guess=res["guess"], reason=res["reason"])
                summary["text"] = vocab.publisher_resolution_text(summary)
                extra["entity_resolution"] = summary
            except Exception as exc:                           # noqa: BLE001
                extra["entity_resolution"] = {"status": "error", "text": "",
                                              "error": repr(exc)[:200]}
        self.emit("search_plan", round=self.round + 1,
                  paths=[_path_chip(m) for m in methods
                         if m not in [s["id"] for s in skipped]],
                  skipped=skipped,
                  place=place or None,
                  area=area,
                  area_used=bool("spatial" in methods and place),
                  entity=entity or None,
                  graph_relation=vocab.intent_label(intent) or None, **extra)
        self.step_end("route", "%d search paths" % len(methods))

    # retrieve
    def _retrieve_start(self, p):
        plan = p.get("plan") or {}
        methods = plan.get("methods") or []
        n = 0
        for m in methods:
            if m in ("keyword", "semantic"):
                n += 1
            elif m == "spatial" and plan.get("place"):
                n += 1
            elif m == "graph" and plan.get("graph_intent"):
                n += 1
        self._path_query = {}
        self.step_start("retrieve", "search", "Searching %d source%s…"
                        % (n, "" if n == 1 else "s"), round=self.round)

    def _retrieve_end(self, p):
        sizes = p.get("sizes") or {}
        self.step_end("retrieve", ", ".join(
            "%s %d" % (vocab.path_label(k), v) for k, v in sizes.items()))

    # rewrite
    def _rewrite_end(self, p):
        entry, result, question = p.get("entry"), p["result"], p["question"]
        kept = result == question
        status = (entry or {}).get("status", "")
        if entry is None:
            outcome = "Your wording was used as written."
        else:
            outcome = _REWRITE_OUTCOMES.get(
                status, "Your wording was kept." if kept
                else "Rewritten to match the catalogue's wording.")
        # A rewrite the system produced and then refused to use (it dropped the
        # place or name, or used words the catalogue lacks).
        attempted, attempted_reason, attempted_dropped = None, None, []
        if entry and status in _REJECTED_REASONS:
            first = rewrite._first_line(entry.get("raw", ""))
            if first and first != question:
                attempted = first
                attempted_reason = _REJECTED_REASONS[status]
                # Which of the strings the plan asked the rewrite to keep are
                # not in it.
                if status == "fallback:must_keep":
                    low = first.lower()
                    dropped = [k for k in (entry.get("must_keep") or [])
                               if k and str(k).lower() not in low]
                    if dropped:
                        attempted_dropped = [str(k) for k in dropped]
                        attempted_reason = ("it dropped %s from your question"
                                            % " and ".join(
                                                "\u2018%s\u2019" % k
                                                for k in attempted_dropped))
        self.emit("query_rewrite", round=self.round, input=question,
                  rewritten=None if kept else result, kept_wording=kept,
                  attempted=attempted, attempted_reason=attempted_reason,
                  attempted_dropped=attempted_dropped,
                  outcome=outcome,
                  required_terms=list((entry or {}).get("required") or []),
                  protected_terms=list((entry or {}).get("must_keep") or []),
                  dropped_terms=list((entry or {}).get("dropped") or []))

    # the four paths
    def _path_start(self, p):
        path = p["path"]
        self._path_query[path] = p
        self.emit("path_started", round=self.round, path=path,
                  label=vocab.path_label(path),
                  query=p.get("query") or p.get("entity") or "")

    def _path_end(self, p):
        path = p["path"]
        started = self._path_query.get(path, {})
        rows = p.get("rows") or []
        self.emit("path_results", round=self.round, path=path,
                  label=vocab.path_label(path),
                  color=vocab.PATH_COLORS.get(path),
                  query=started.get("query") or started.get("entity") or "",
                  place=started.get("place"),
                  intent=vocab.intent_label(started.get("intent")) or None,
                  entity=started.get("entity") or None,
                  count=len(rows), duration_ms=int(p["seconds"] * 1000),
                  rows=[{"rank": i + 1, **r} for i, r in enumerate(rows)])

    def _path_error(self, p):
        path = p.get("path")
        self.errors += 1

    # fusion
    def _fuse_start(self, p):
        inputs = p.get("inputs") or {}
        total = sum(inputs.values())
        self.step_start("fuse", "combine", "Combining %d results…" % total,
                        round=self.round)

    def _fuse_end(self, p):
        inputs = p.get("inputs") or {}
        across = bool(inputs) and all(k.startswith("round") for k in inputs)
        rows = []
        for i, d in enumerate(p.get("docs") or []):
            rows.append({"rank": i + 1, "id": d.get("id"),
                         "title": d.get("title", ""),
                         "organization": d.get("organization", ""),
                         "type": d.get("type", ""),
                         "score": d.get("rrf_score"),
                         "paths": _path_names(d),
                         "path_ranks": _one_based(d.get("path_ranks"))})
        self.emit("fusion", round=None if across else self.round,
                  scope="all_rounds" if across else "round",
                  inputs=inputs, count=len(rows), rows=rows)
        self.step_end("fuse", "%d combined" % len(rows))

    # decide / clarify
    def _decide_start(self, p):
        self.step_start("decide", "decide",
                        "Deciding whether more evidence is needed…",
                        round=self.round)

    def _decide_end(self, p):
        decision = p.get("decision") or {}
        sub = str(decision.get("subquery") or "").strip()
        search = decision.get("action") == "search" and bool(sub)
        text = ("Agent decision: search again for \"%s\"" % sub if search
                else "Agent decision: enough evidence")
        self.emit("decision", round=self.round,
                  action="search" if search else "answer",
                  subquery=sub if search else None, text=text)
        self.step_end("decide", text)

    def _clarify_start(self, p):
        self.step_start("clarify_check", "clarify_check",
                        "Checking whether a clarifying question would help…",
                        round=self.round)

    def _clarify_end(self, p):
        verdict = p.get("verdict") or {}
        asked = bool(verdict.get("ask"))
        # Only the grounded rule reports its features.
        extra = {}
        if verdict.get("features") is not None:
            extra = {"features": verdict["features"],
                     "reason": vocab.grounded_reason(verdict["features"])}
        self.emit("clarify_check", round=self.round, asked=asked,
                  question=verdict.get("question") or None, **extra)
        self.step_end("clarify_check",
                      "Will ask a clarifying question" if asked
                      else "No clarifying question needed")

    # rerank / finalise
    def _rerank_start(self, p):
        # The order handed to the reranker. It is only in the start record:
        # the end record carries the order that came back.
        self._rerank_before = p.get("before") or []
        self.step_start("rerank", "rerank", "Re-ranking %d candidates…"
                        % p.get("candidates", 0))

    def _rerank_end(self, p):
        before = {b["id"]: i + 1 for i, b in enumerate(self._rerank_before)}
        after = p.get("after") or []
        top_k = config.TOP_K
        rows = []
        for i, d in enumerate(after):
            rows.append({"id": d.get("id"), "title": d.get("title", ""),
                         "organization": d.get("organization", ""),
                         "rank_before": before.get(d.get("id")),
                         "rank_after": i + 1,
                         "relevance_score": d.get("rerank_score"),
                         "combined_score": d.get("vote_score"),
                         "fusion_score": d.get("rrf_score"),
                         "moved_in": bool(i < top_k and before.get(d.get("id"), 0) > top_k)})
            self._rerank_rows[d.get("id")] = (before.get(d.get("id")), i + 1)
        trace = p.get("trace") or {}
        names = {"cross": "Relevance model", "graph": "Knowledge graph relation",
                 "publisher": "Publisher you named"}
        signals = [{"id": v, "label": names.get(v, v)}
                   for v in (trace.get("voices") or [])]
        self.emit("rerank", candidates=len(after), top_k=top_k,
                  top=rows[:top_k],
                  scores=[{"id": r["id"], "rank_before": r["rank_before"],
                           "rank_after": r["rank_after"],
                           "relevance_score": r["relevance_score"]}
                          for r in rows],
                  moved_in=[r["id"] for r in rows[:top_k] if r["moved_in"]],
                  signals=signals,
                  graph_members=trace.get("graph_members"),
                  duration_ms=int(p["seconds"] * 1000))
        self.step_end("rerank", "%d moved into the top %d"
                      % (sum(1 for r in rows[:top_k] if r["moved_in"]), top_k))

    def _finalise_end(self, p):
        items = []
        for i, d in enumerate(p.get("top") or []):
            before, after = self._rerank_rows.get(d.get("id"), (None, i + 1))
            items.append({
                "rank": i + 1, "id": d.get("id"), "title": d.get("title", ""),
                "organization": d.get("organization", ""),
                "type": d.get("type", ""),
                "description": short_text(d.get("description", ""), 300),
                "tags": list(d.get("tags") or [])[:5],
                "url": d.get("url", ""),
                "paths": [_path_chip(x) for x in _path_names(d)],
                "path_ranks": _one_based(d.get("path_ranks")),
                "rank_before": before, "rank_after": i + 1,
                "relevance_score": d.get("rerank_score"),
                "combined_score": d.get("vote_score"),
                "fusion_score": d.get("rrf_score"),
                "relation": d.get("graph_note") or None,
            })
        self.final_docs = [{"id": d.get("id"), "title": d.get("title", "")}
                           for d in p.get("top") or []]
        self.emit("results", count=len(items), items=items)

    # generation
    def _generate_start(self, p):
        label = "Writing answer…" if self.attempts == 0 else "Revising answer…"
        self.step_start("generate", "write", label, attempt=self.attempts + 1)

    def _generate_end(self, p):
        self.attempts += 1
        self.emit("answer_draft", attempt=self.attempts, text=p["text"],
                  revised_because=p.get("feedback") or None,
                  relation_hint=bool(p.get("hint_used")))
        self.step_end("generate", "Draft %d" % self.attempts)

    def _validate_start(self, p):
        # Where this check's lenient acceptances will start in the trace.
        self._cite_mark = len(pipeline.CITE_TRACE)
        self.step_start("validate", "check",
                        "Checking answer against sources…",
                        attempt=self.attempts)

    def _validate_end(self, p):
        grounded, relevant = p["grounded"], p["relevant"]
        raw_g = p.get("citation_detail") or ""
        problems = []
        if raw_g.startswith("NO (citation check)"):
            problems = [s.strip() for s in raw_g.split(": ", 1)[-1].split(";")
                        if s.strip()]
        elif not grounded:
            problems = ["The answer made claims the evidence does not support."]
        if not relevant:
            problems.append("The answer did not address the question.")
        passed = bool(grounded and relevant)
        if passed:
            self.answer_passed = True
        # Ids the lenient citation rule let through in this check (read from
        # the system's trace; empty under the strict default).
        accepted = [dict(a) for a in pipeline.CITE_TRACE[self._cite_mark:]]
        rank_of = {d["id"][:8].lower(): i + 1
                   for i, d in enumerate(self.final_docs)}
        self.emit("answer_check", attempt=self.attempts, grounded=grounded,
                  relevant=relevant, passed=passed, problems=problems,
                  lenient_accepted=len(accepted),
                  lenient_types={t: sum(1 for a in accepted if a.get("type") == t)
                                 for t in ("title", "description", "publisher")
                                 if any(a.get("type") == t for a in accepted)},
                  lenient_detail=[{"rank": rank_of.get(a["id"].lower()),
                                   "type": a.get("type"), "rule": a.get("rule"),
                                   "shared": a["shared"]} for a in accepted],
                  detail={"grounded": raw_g[:400],
                          "relevant": (p.get("relevance_detail") or "")[:200]})
        self.step_end("validate", "Passed" if passed else "Needs revision")

    # log lines
    def _stdout_line(self, p):
        found = notice_for(p.get("line", ""))
        if found:
            text, detail = found
            self.emit("notice", level="warning", text=text, detail=detail)

    # -- stage stream --
    def on_stage(self, stage, message):
        if self.recorder is not None:
            self.recorder.stage(stage, message)
        if stage == "retrieve":
            self.round += 1
            m = re.match(r"iteration \d+: (.*)$", message, re.S)
            self.emit("round_started", round=self.round,
                      query=m.group(1) if m else message)
        elif stage == "rerank":
            m = re.match(r"evidence pool: (\d+) elements \(\+(\d+) in the head\)",
                         message)
            if m:
                size, new = int(m.group(1)), int(m.group(2))
                self.emit("evidence_pool", round=self.round, size=size,
                          new_in_top=new,
                          text="%d candidate datasets so far (%d new in the top %d)"
                          % (size, new, config.TOP_K))
        elif stage == "reasoning":
            if message.startswith("evidence pool saturated"):
                self.emit("loop_note", round=self.round, code="saturated",
                          text="Few new results this round, so the search "
                               "moved on to the answer.")
            elif message.startswith("follow-up repeats"):
                self.emit("loop_note", round=self.round, code="repeat",
                          text="The follow-up query repeated an earlier one, "
                               "so the search stopped.")
            elif message.startswith("needs more evidence -> "):
                self.emit("loop_note", round=self.round, code="search_again",
                          subquery=message[len("needs more evidence -> "):],
                          text="Searching again with a follow-up query.")
        elif stage == "ask":
            self.job.expect_reply()
            self.step_start("clarify", "clarify", "Waiting for your answer…")
            # The grounded question carries answer options
            # (pipeline.ClarifyQuestion); a plain question gets no field.
            options = getattr(message, "options", None)
            extra = {} if options is None else {
                "options": [{"label": o.get("label"), "value": o.get("value")}
                            for o in options]}
            self.emit("clarify_question", job_id=self.job.id, question=message,
                      null_reply=config.NULL_REPLY, can_skip=True,
                      timeout_s=self.engine.ask_timeout, **extra)
        elif stage == "ask_reply":
            informative = pipeline.informative(message)
            self.step_end("clarify", "Answered" if informative else "Skipped")
            self.emit("clarify_reply", reply=message, skipped=not informative,
                      timed_out=bool(self.job.timed_out),
                      restarts=bool(informative))
        elif stage == "ask_restart":
            self.emit("restart", query=message,
                      text="Searching again with your clarification")
            self.run_index += 1
            self.round = 0
            self.attempts = 0
        elif stage == "generation":
            self.emit("progress", label="Writing answer…")
        elif stage == "answer":
            self._answer(message)

    def _answer(self, text):
        docs = [{"id": d["id"]} for d in self.final_docs]
        cited, collisions, unresolved = pipeline.resolve_citations(text, docs)
        titles = {d["id"]: d["title"] for d in self.final_docs}
        by_prefix = {}
        for d in self.final_docs:
            by_prefix.setdefault(d["id"][:8].lower(), []).append(d["id"])
        segments, pos = [], 0
        for m in pipeline.CITATION_RE.finditer(text or ""):
            marker = m.group(1)
            if not re.search(r"[a-f]", marker):
                continue
            start, end = m.start(), m.end()
            # Drop the brackets the model wrote around a lone marker.
            if start > 0 and text[start - 1] == "[" and text[end:end + 1] == "]":
                start, end = start - 1, end + 1
            if start > pos:
                segments.append({"type": "text", "text": text[pos:start]})
            ids = by_prefix.get(marker.lower(), [])
            if len(ids) == 1:
                seg = {"type": "cite", "marker": marker, "status": "resolved",
                       "id": ids[0], "title": titles.get(ids[0], "")}
            elif len(ids) > 1:
                seg = {"type": "cite", "marker": marker, "status": "ambiguous",
                       "ids": ids}
            else:
                seg = {"type": "cite", "marker": marker, "status": "unresolved"}
            segments.append(seg)
            pos = end
        if pos < len(text or ""):
            segments.append({"type": "text", "text": text[pos:]})
        refused = (text or "").strip() == pipeline.REFUSAL
        self.emit("answer", text=text or "", segments=segments,
                  cited=cited, ambiguous=collisions, unresolved=unresolved,
                  attempts=self.attempts, refused=refused,
                  checks_passed=self.answer_passed)


# ---- the engine -----------------------------------------------------------
class Engine:
    """Serialises jobs and runs them. run() blocks until the job is over."""

    def __init__(self, corpus, ask_timeout=300.0):
        self.corpus = corpus
        self.ask_timeout = ask_timeout
        self._cv = threading.Condition()
        self._next_ticket = 0
        self._serving = 0
        self.current = None
        self.jobs_done = 0

    # -- queue --
    @property
    def busy(self):
        return self.current is not None

    def waiting(self):
        with self._cv:
            return max(0, self._next_ticket - self._serving - (1 if self.current else 0))

    def take_ticket(self):
        with self._cv:
            ticket = self._next_ticket
            self._next_ticket += 1
            return ticket, ticket - self._serving

    def _wait_turn(self, ticket):
        with self._cv:
            while self._serving != ticket:
                self._cv.wait(0.5)

    def _finish_turn(self):
        with self._cv:
            self._serving += 1
            self._cv.notify_all()

    # -- traces --
    @staticmethod
    def reset_traces():
        """What eval/run_eval.py does around every row."""
        rewrite.reset_trace()
        rerank.reset_trace()
        retrieval.reset_hydration_trace()
        pipeline.reset_ask_trace()
        pipeline.reset_clarify_trace()
        pipeline.reset_pool_trace()
        pipeline.reset_cite_trace()

    # -- the run --
    def run(self, job, ticket=None):
        if ticket is None:
            ticket, _ = self.take_ticket()
        sink = _JobSink(self, job)
        self._wait_turn(ticket)
        try:
            self.current = job
            sink.t0 = time.monotonic()
            sink.emit("job_started", job_id=job.id, corpus=job.corpus,
                      question=job.question, history=len(job.history),
                      ask=bool(job.ask and self._ask_available()))
            if job.cancelled:
                sink.emit("done", ok=False, cancelled=True, steps=0,
                          duration_s=sink.elapsed())
                return
            self._run_pipeline(job, sink)
        finally:
            self.current = None
            self.jobs_done += 1
            self._finish_turn()

    @staticmethod
    def _comparison(sink):
        """Emit the comparison event (ui/compare.py). A column that fails is
        reported in the event; nothing here may fail the job."""
        sink.emit("progress", label="Comparing with simpler searches…")
        rec = sink.recorder
        final = rec.final or []
        try:
            backend = compare.Backend(
                observe.original(retrieval, "keyword_search"),
                retrieval.dedup_by_title,
                observe.original(retrieval, "finalise"),
                rerank.TRACE)
            data = compare.build(rec.runs, final, backend, top_k=config.TOP_K)
        except Exception as exc:                               # noqa: BLE001
            data = compare.unavailable(
                rec.runs, final,
                "The comparison could not be computed (%s)." % type(exc).__name__,
                top_k=config.TOP_K)
        sink.emit("comparison", **data)

    @staticmethod
    def _ask_available():
        return getattr(config, "ASK_MODE", "off") == "on"

    def _run_pipeline(self, job, sink):
        ask_fn = None
        if job.ask and self._ask_available():
            def ask_fn(agent_question):                      # noqa: E306
                reply = job.wait_reply(self.ask_timeout)
                if reply is None or not str(reply).strip():
                    job.timed_out = reply is None and not job.cancelled
                    return config.NULL_REPLY
                return str(reply)

        ok, cancelled = True, False
        compare_on = bool(job.compare and settings.UI_COMPARE)
        if compare_on:
            sink.capture_pools = True
            sink.recorder = compare.PoolRecorder(job.question)
        self.reset_traces()
        sink.emit("progress", label="Understanding your question…")
        stream = None
        try:
            with observe.activate(sink):
                stream = pipeline.run(job.question, job.history, ask_fn)
                for stage, message in stream:
                    sink.on_stage(stage, message)
                    if job.cancelled:
                        cancelled = True
                        break
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            ok = False
            kind, plain, detail = describe_error(exc)
            sink.emit("error", kind=kind, message=plain,
                      detail=detail[:2000],
                      trace=traceback.format_exc()[-2000:])
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:                              # noqa: BLE001
                    pass
            self.reset_traces()
        if compare_on and ok and not cancelled:
            # After the answer, outside the sink: the columns re-run keyword
            # search and the re-ranker, and none of that may show up as steps
            # of this job or slow its answer down.
            self._comparison(sink)
        sink.emit("done", ok=ok and not cancelled, cancelled=cancelled,
                  steps=sink.steps, duration_s=sink.elapsed(),
                  recording_errors=observe.RECORDING_ERRORS["count"])
