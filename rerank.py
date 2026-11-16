"""Cross-encoder reranking over the fused candidate pool."""
import config

_model = None
_model_name = None

# Filled in by rerank(); eval/run_eval.py reads it so the report can quantify
# how much reordering actually happened rather than asserting that it did.
TRACE = {"calls": 0, "docs": 0, "moved_into_top_k": 0, "positions_moved": 0}
_COUNTERS = tuple(TRACE)


def active():
    return getattr(config, "RERANK_MODE", "off") not in ("off", "", None)


def reset_trace():
    # Counters go back to zero; anything else (RERANK_QUERY=auto's per-row
    # "query" entry) is removed.
    for key in list(TRACE):
        if key in _COUNTERS:
            TRACE[key] = 0
        else:
            del TRACE[key]


def take_trace():
    snapshot = dict(TRACE)
    reset_trace()
    return snapshot


def _get_model():
    """Loaded lazily, for the same reason retrieval.get_embedder() is: do not
    grab a compute context before Ollama has loaded the generator."""
    global _model, _model_name
    name = getattr(config, "RERANK_MODEL", "BAAI/bge-reranker-base")
    if _model is None or _model_name != name:
        from sentence_transformers import CrossEncoder
        device = getattr(config, "RERANK_DEVICE", "cpu")
        _model = CrossEncoder(name, device=device,
                              max_length=getattr(config, "RERANK_MAX_LEN", 512))
        _model_name = name
        print("[info] rerank: loaded %s on %s (pool=%d)"
              % (name, device, getattr(config, "RERANK_POOL", 50)))
    return _model


def doc_text(doc):
    """What the cross-encoder reads on the document side."""
    parts = []
    title = (doc.get("title") or "").strip()
    if title:
        parts.append(title + ".")
    org = (doc.get("organization") or "").strip()
    if org:
        parts.append("Published by %s." % org)
    # Known issue, left as is: retrieval.to_doc() already cuts tags to 6, so
    # the 8 here never takes effect.
    tags = [t for t in (doc.get("tags") or []) if t][:8]
    if tags:
        parts.append("Tags: %s." % ", ".join(tags))
    desc = (doc.get("description") or "").strip()
    if desc:
        parts.append(desc[:getattr(config, "RERANK_DESC_CHARS", 400)])
    # DOC_EXTRA_MODE. Last, after the description: when the pair is longer than
    # RERANK_MAX_LEN the tokenizer cuts from the end, so the added sentence is
    # the first thing to go and the record's own text is never displaced.
    extra = doc_extra(doc)
    if extra:
        parts.append(extra)
    return " ".join(parts) or "(no metadata)"


# ---- DOC_EXTRA_MODE=append (not used by the evaluated configuration) ------
_doc_extra = {"path": None, "table": None, "sha256": None}


def doc_extra_mode():
    mode = getattr(config, "DOC_EXTRA_MODE", "off") or "off"
    if mode not in ("off", "append"):
        raise ValueError("unknown DOC_EXTRA_MODE %r; use off or append" % mode)
    return mode


def _doc_extra_table():
    """{id: [queries]} from DOC_EXTRA_FILE, read once per path."""
    path = getattr(config, "DOC_EXTRA_FILE", "") or ""
    if not path:
        raise ValueError("DOC_EXTRA_MODE=append needs DOC_EXTRA_FILE")
    if _doc_extra["path"] != path:
        import hashlib
        import json
        with open(path, "rb") as f:
            blob = f.read()
        table = {}
        for line in blob.decode("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                table[row["id"]] = [q for q in (row.get("queries") or []) if q]
        _doc_extra.update(path=path, table=table,
                          sha256=hashlib.sha256(blob).hexdigest())
    return _doc_extra["table"]


def doc_extra(doc):
    """The sentence DOC_EXTRA_MODE=append adds for `doc`, or '' -- always '' when off, and
    when the file has no queries for this id."""
    if doc_extra_mode() == "off":
        return ""
    queries = _doc_extra_table().get(doc.get("id"))
    if not queries:
        return ""
    return "Users might search: %s." % "; ".join(queries)


def doc_extra_fingerprint():
    """{} when off."""
    if doc_extra_mode() == "off":
        return {}
    _doc_extra_table()
    return {"doc_extra": {"mode": doc_extra_mode(),
                          "file": _doc_extra["path"],
                          "sha256": _doc_extra["sha256"]}}


def query_text(question, doc):
    """What the cross-encoder reads on the query side, for this document."""
    if not getattr(config, "GRAPH_QUERY_REWRITE", False):
        return question
    anchor = (doc.get("graph_anchor") or "").strip()
    if not anchor:
        return question
    return ("%s Datasets related to, or from the same source as: %s"
            % (question, anchor[:120]))


def scoring_query(question):
    """The text the cross-encoder scores for `question`."""
    mode = getattr(config, "RERANK_QUERY", "original")
    if mode == "original":
        return question, "original"
    if mode != "auto":
        raise ValueError("unknown RERANK_QUERY %r; use original or auto" % mode)
    import rewrite                  # only reached under auto
    entry = rewrite.outcome_for(question)
    if (entry is not None
            and rewrite.gate_reason_family(entry) == "non_latin"
            and str(entry.get("status", "")).startswith("ok")
            and entry.get("rewritten") and entry["rewritten"] != question):
        return entry["rewritten"], "rewrite"
    return question, "original"


# ---- RERANK_MODE=vote --------------------------------------------------------
KNOWN_VOICES = ("cross", "graph", "fusion")
# The graph intents whose relation the cross-encoder cannot read: the other end
# of the relation is the anchor document, which is not in the candidate's own
# text.
GRAPH_VOICE_RELATIONS = ("PROVENANCE", "CO_CONTRIBUTION")
# ENTITY_RESOLVE=voice|both|link_both (not used by the evaluated
# configuration).
PUBLISHER_VOICE = "publisher"


def publisher_voice_active():
    return getattr(config, "ENTITY_RESOLVE", "off") in ("voice", "both", "link_both")
_warned_unhydrated = [False]


def vote_voices():
    """The configured voices, validated. 'cross' is required."""
    raw = getattr(config, "RERANK_VOICES", "cross") or "cross"
    voices = [v.strip().lower() for v in raw.split(",") if v.strip()]
    unknown = [v for v in voices if v not in KNOWN_VOICES]
    if unknown:
        raise ValueError("unknown RERANK_VOICES entries %s; known: %s (the llm "
                         "voice is designed but not implemented)"
                         % (unknown, ", ".join(KNOWN_VOICES)))
    if "cross" not in voices:
        raise ValueError("RERANK_VOICES must include 'cross': the cross-encoder "
                         "is the primary ranker and the only voice guaranteed "
                         "to cover the whole pool")
    ordered = [v for v in KNOWN_VOICES if v in voices]   # fixed summation order
    return ordered


def graph_voice_ranks(docs):
    """{doc index: rank} for the 'graph' voice, or {} when nothing qualifies."""
    members = [i for i, d in enumerate(docs)
               if d.get("graph_relation") in GRAPH_VOICE_RELATIONS]

    def key(i):
        w = docs[i].get("graph_weight")
        return (0, 0.0) if w is None else (1, float(w))

    return {i: sum(1 for j in members if key(j) > key(i)) for i in members}


def vote_order(docs, cross_order, voices, absent=None):
    """Merge the voices by RRF (k = RRF_K) over their ranks."""
    absent = absent or getattr(config, "VOTE_ABSENT_RANK", "none")
    if absent not in ("none", "tail"):
        raise ValueError("unknown VOTE_ABSENT_RANK %r; use none or tail" % absent)
    ranks = {"cross": {i: r for r, i in enumerate(cross_order)}}
    skipped = {}
    for voice in voices:
        if voice == "fusion":
            ranks["fusion"] = {i: i for i in range(len(docs))}
        elif voice == "graph":
            graph = graph_voice_ranks(docs)
            if graph:
                ranks["graph"] = graph
            else:
                skipped["graph"] = "no %s graph result in the pool" % (
                    "/".join(GRAPH_VOICE_RELATIONS))
        elif voice == PUBLISHER_VOICE:
            # Records of the resolved publisher, in their pool order; the
            # rest are absent, exactly as outside the graph voice.
            members = [i for i, d in enumerate(docs) if d.get("publisher_match")]
            if members:
                ranks[PUBLISHER_VOICE] = {i: r for r, i in enumerate(members)}
            else:
                skipped[PUBLISHER_VOICE] = "no record of a resolved publisher in the pool"
    used = [v for v in KNOWN_VOICES + (PUBLISHER_VOICE,) if v in ranks]
    if used == ["cross"]:
        return list(cross_order), used, ranks, skipped

    k = config.RRF_K

    def score(i):
        total = 0.0
        for voice in used:
            rv = ranks[voice]
            if i in rv:
                total += 1.0 / (k + rv[i] + 1)
            elif absent == "tail":
                total += 1.0 / (k + len(rv) + 1)
        return total

    scores = {i: score(i) for i in range(len(docs))}
    for i, doc in enumerate(docs):
        doc["vote_score"] = round(scores[i], 8)
    return (sorted(range(len(docs)), key=lambda i: (-scores[i], i)),
            used, ranks, skipped)


def cross_scores(question, docs):
    """Cross-encoder scores for `docs`, and the query text they were scored
    against (scoring_query() decides which)."""
    query, source = scoring_query(question)
    model = _get_model()
    pairs = [(query_text(query, doc), doc_text(doc)) for doc in docs]
    scores = model.predict(pairs,
                           batch_size=getattr(config, "RERANK_BATCH", 32),
                           show_progress_bar=False)
    return [float(s) for s in scores], query, source


def rerank(question, docs):
    """Reorder `docs` by cross-encoder relevance to `question`."""
    if not active() or len(docs) < 2:
        return docs

    mode = getattr(config, "RERANK_MODE", "off")
    if mode not in ("cross", "vote"):
        raise ValueError(
            "unknown RERANK_MODE %r; use cross or vote. Blending the "
            "cross-encoder score with the RRF score is a second mechanism "
            "and must be defined as its own variant before being measured."
            % mode)
    voices = vote_voices() if mode == "vote" else ["cross"]
    if mode == "vote" and publisher_voice_active():
        voices = voices + [PUBLISHER_VOICE]
    if mode == "vote" and "graph" in voices and not _warned_unhydrated[0] \
            and not getattr(config, "HYDRATE_CANDIDATES", False):
        _warned_unhydrated[0] = True
        print("[warn] rerank: graph voice with HYDRATE_CANDIDATES=0 -- the "
              "cross-encoder also reads the relation text leaked into graph "
              "results, so the graph relation is counted twice")

    scores, query, source = cross_scores(question, docs)
    if getattr(config, "RERANK_QUERY", "original") != "original":
        # Which text this row was scored against. Only written when the
        # switch is set, so default runs keep their historical trace fields.
        TRACE["query"] = source
        if source == "rewrite":
            TRACE["query_text"] = query

    before = [doc["id"] for doc in docs]
    for doc, score in zip(docs, scores):
        doc["rerank_score"] = round(score, 6)

    # Stable: ties fall back to the incoming (fused) rank.
    order = sorted(range(len(docs)), key=lambda i: (-scores[i], i))
    if mode == "vote":
        order, used, ranks, skipped = vote_order(docs, order, voices)
        # Which voices actually ranked this row. Written only in vote mode.
        TRACE["voices"] = used
        TRACE["graph_members"] = len(ranks.get("graph", {}))
        if publisher_voice_active():
            TRACE["publisher_members"] = len(ranks.get(PUBLISHER_VOICE, {}))
        if skipped:
            TRACE["skipped"] = skipped
    out = [docs[i] for i in order]

    top_k = config.TOP_K
    head_before = set(before[:top_k])
    head_after = [doc["id"] for doc in out[:top_k]]
    TRACE["calls"] += 1
    TRACE["docs"] += len(docs)
    TRACE["moved_into_top_k"] += sum(1 for i in head_after
                                     if i not in head_before)
    position = {eid: rank for rank, eid in enumerate(before)}
    TRACE["positions_moved"] += sum(
        abs(position[doc["id"]] - rank) for rank, doc in enumerate(out))
    return out


def fingerprint():
    """Goes into the run file so a reranked run can never be mistaken for an
    unreranked one, or one reranker for another."""
    if not active():
        return {"mode": "off"}
    vote = {}
    if getattr(config, "RERANK_MODE", "off") == "vote":
        vote = {"voices": vote_voices(),
                "absent_rank": getattr(config, "VOTE_ABSENT_RANK", "none"),
                "graph_relations": list(GRAPH_VOICE_RELATIONS),
                "rrf_k": config.RRF_K}
        # Present only when not the default value.
        if getattr(config, "GRAPH_VOICE_ROUNDS", "all") != "all":
            vote["graph_voice_rounds"] = config.GRAPH_VOICE_ROUNDS
    return {**vote,
        "mode": getattr(config, "RERANK_MODE", "off"),
        "model": getattr(config, "RERANK_MODEL", ""),
        "pool": getattr(config, "RERANK_POOL", 0),
        "desc_chars": getattr(config, "RERANK_DESC_CHARS", 0),
        "max_len": getattr(config, "RERANK_MAX_LEN", 0),
        "graph_query_rewrite": bool(getattr(config, "GRAPH_QUERY_REWRITE",
                                            False)),
        # Present only when not the default value.
        **({"rerank_query": getattr(config, "RERANK_QUERY")}
           if getattr(config, "RERANK_QUERY", "original") != "original" else {}),
        # Same rule: absent under DOC_EXTRA_MODE=off.
        **doc_extra_fingerprint(),
        # Same rule: absent under ENTITY_RESOLVE=off.
        **({"entity_resolve": getattr(config, "ENTITY_RESOLVE")}
           if getattr(config, "ENTITY_RESOLVE", "off") != "off" else {}),
    }


__all__ = ["active", "rerank", "doc_text", "query_text", "scoring_query",
           "doc_extra", "doc_extra_mode", "doc_extra_fingerprint",
           "vote_voices", "vote_order", "graph_voice_ranks", "cross_scores",
           "GRAPH_VOICE_RELATIONS", "PUBLISHER_VOICE", "publisher_voice_active",
           "fingerprint",
           "reset_trace", "take_trace", "TRACE"]
