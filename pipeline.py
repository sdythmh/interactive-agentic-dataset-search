"""The agentic search pipeline."""
import hashlib
import json
import re

from openai import OpenAI

import config
import expansion
import hyde
import retrieval
import rewrite

llm = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)

REFUSAL = "I don't have enough information in the indexed catalog to answer this."


def call_llm(system, user, temperature=0.2):
    # config.LLM_SEED = None sends no seed.
    extra = {} if config.LLM_SEED is None else {"seed": config.LLM_SEED}
    resp = llm.chat.completions.create(
        model=config.LLM_MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **extra,
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        print("[warn] empty LLM response")
    return text


def parse_json(text, fallback):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return fallback
    try:
        return json.loads(match.group())
    except Exception:
        return fallback


# ---- Memory rewrite -------------------------------------------------------
MEMORY_SYSTEM = (
    "Rewrite the new question so it can be understood on its own, replacing "
    "pronouns and phrases like 'those' or 'there' with what they refer to in "
    "the previous turn. If it already stands alone, repeat it unchanged. "
    "Return only the question, one line, no explanation."
)


def memory_rewrite(question, history):
    if not history:
        return question
    previous = "\n".join(
        "%s: %s" % (m["role"], m["content"][:300]) for m in history[-2:]
    )
    out = call_llm(MEMORY_SYSTEM,
                   "Previous turn:\n%s\n\nNew question: %s" % (previous, question))
    rewritten = out.splitlines()[0].strip().strip('"').strip() if out else ""
    if not rewritten or len(rewritten) > 300:
        return question
    return rewritten


# ---- Routing --------------------------------------------------------------
# The prompts below are English templates. The words that name the catalogue
# come from the corpus (config.PROMPT_*_DOMAIN, see corpora/au/corpus.py) and
# are spliced in by concatenation, not str.format, because this prompt holds
# literal JSON braces. The line break after each fragment belongs to the
# template.
ROUTER_SYSTEM = "You route a search query to retrieval methods for " \
    + config.PROMPT_ROUTER_DOMAIN + """
open-data catalog. Respond ONLY with JSON:
{"methods": [...], "place": "...", "entity": "...", "graph_intent": "..."}

methods: subset of ["keyword", "semantic", "spatial", "graph"]
place: location name if the query has a geographic constraint, else ""
entity: for graph queries, the topic word or the dataset title fragment the
        question is about, else ""
graph_intent: one of AUTHORSHIP, PROVENANCE, CO_CONTRIBUTION, AGGREGATION, or ""

Rules: exact names/ids -> keyword. Conceptual topics -> semantic (usually
include keyword too). Location mentioned -> add spatial.
"datasets related to X" / "similar to X" / "derived from X" -> graph,
PROVENANCE, entity = X.
"published by X" -> graph, AUTHORSHIP, entity = X.
"same publisher as X" / "from the same agency as X" -> graph, CO_CONTRIBUTION,
entity = X.
Use AGGREGATION only for questions about which organisation has the most
datasets.
The catalog has no usage statistics, so there is no popularity intent."""

# Hand-curated trigger patterns. They are written in the corpus's
# language, so they come from corpora/<name>/language.py.
TRIGGERS = config.LANGUAGE.ROUTE_TRIGGERS


def route(question):
    default = {"methods": ["keyword", "semantic"], "place": "", "entity": "",
               "graph_intent": ""}
    plan = parse_json(call_llm(ROUTER_SYSTEM, question), default)

    methods = set(plan.get("methods") or [])
    methods.add("keyword")
    methods.add("semantic")

    if TRIGGERS["spatial"].search(question):
        methods.add("spatial")
    if TRIGGERS["provenance"].search(question):
        methods.add("graph")
        plan["graph_intent"] = plan.get("graph_intent") or "PROVENANCE"
    elif TRIGGERS["authorship"].search(question):
        methods.add("graph")
        plan["graph_intent"] = plan.get("graph_intent") or "AUTHORSHIP"

    if plan.get("graph_intent") and "graph" not in methods:
        methods.add("graph")
    if "graph" in methods and not plan.get("graph_intent"):
        methods.discard("graph")
    if plan.get("graph_intent") not in (
            "", None, "AUTHORSHIP", "PROVENANCE", "CO_CONTRIBUTION", "AGGREGATION"):
        print("[warn] unknown graph_intent %r, dropping graph"
              % plan["graph_intent"])
        plan["graph_intent"] = ""
        methods.discard("graph")

    plan["methods"] = sorted(methods)
    return plan


# ---- One retrieval round --------------------------------------------------
def retrieve(question, plan):
    """Run every retrieval path selected by the plan, optionally expanded."""
    rank_lists = {}
    phrase = plan.get("entity") or ""

    exp = expansion.expand(question, place=plan.get("place", ""), plan=plan)
    terms = exp["terms"]
    if terms:
        preview = ", ".join(terms[:6]) + (" ..." if len(terms) > 6 else "")
        print("[info] expansion terms: %s" % preview)

    # c1 / c2. The rewrite runs here, i.e. after route() has already read the
    # original question, and it is checked against the router's own place and
    # entity: a rewrite that loses either is thrown away by rewrite() itself.
    lexical_q = rewrite.rewrite(
        question, must_keep=[plan.get("place"), plan.get("entity")])

    # Every path is asked for the same number of candidates, and that number is
    # a config value (FUSION_DEPTH, default TOP_K) rather than each retriever's
    # own default.
    depth = getattr(config, "FUSION_DEPTH", config.TOP_K)

    if "keyword" in plan["methods"]:
        rank_lists["keyword"] = retrieval.keyword_search(
            lexical_q, size=depth, phrase=phrase, expansion_terms=terms)
    if "semantic" in plan["methods"]:
        # With HYDE on, a hypothetical-document vector replaces the query
        # vector on this path and nothing else.
        vector = (hyde.query_vector(question, lexical_q, retrieval.encode_query)
                  if hyde.active() else None)
        rank_lists["semantic"] = retrieval.semantic_search(lexical_q, size=depth,
                                                           vector=vector)
    if "spatial" in plan["methods"] and plan.get("place"):
        # The spatial and graph paths keep the original question: their
        # selectivity comes from plan['place'] and plan['entity'], which the
        # rewrite is deliberately not allowed to touch.
        rank_lists["spatial"] = retrieval.spatial_search(
            plan["place"], query=question, size=depth, phrase=phrase,
            expansion_terms=terms)
    if "graph" in plan["methods"] and plan.get("graph_intent"):
        intent = plan["graph_intent"]
        entity = plan.get("entity") or ""
        if not entity and intent != "AGGREGATION":
            entity = question
        # The member intents can rank by the question
        # (GRAPH_MEMBER_ORDER=query); like the spatial path they get the
        # original question, not the rewrite.
        rank_lists["graph"] = retrieval.graph_search(intent, entity, size=depth,
                                                     query=question)
    # Publisher linking (ENTITY_RESOLVE). Off: nothing below runs. Every
    # round resolves again, on its own question and plan.
    mode = getattr(config, "ENTITY_RESOLVE", "off")
    if mode != "off":
        # link_*: the model links the publisher; otherwise the word-set rule.
        if mode.startswith("link_"):
            res = retrieval.link_publisher(plan.get("entity"), question, plan)
        else:
            res = retrieval.resolve_publisher(plan.get("entity"), question)
        _record_publisher(res, question, plan)
        if res["status"] == "resolved":
            if mode in ("path", "both", "link_path", "link_both"):
                # Last in the dict, so it enters RRF after the other paths.
                rank_lists["publisher"] = retrieval.publisher_search(
                    res["ids"], lexical_q, size=depth, phrase=phrase,
                    expansion_terms=terms)
            if mode in ("voice", "both", "link_both"):
                # The mark the re-ranker's publisher voice reads, written now
                # on every projection of a resolved record, so whichever one
                # rrf_fuse keeps carries it (and so does any copy taken of the
                # fused pool).
                ids = set(res["ids"])
                for docs in rank_lists.values():
                    for doc in docs:
                        if doc["id"] in ids:
                            doc["publisher_match"] = True
    return rank_lists


# Publisher linking. What retrieve() resolved, one entry per call, since the
# start of the current run() (eval/run_eval.py resets it per row for c_routed).
PUBLISHER_TRACE = []
_PUBLISHER_IDS = []


def _record_publisher(res, question, plan):
    PUBLISHER_TRACE.append({
        "question": question, "entity": res["entity"],
        "graph_intent": (plan or {}).get("graph_intent") or "",
        "status": res["status"], "source": res["source"],
        "names": res["names"][:10], "n_names": len(res["names"]),
        "n_ids": len(res["ids"]), "family_size": res["family_size"],
        "cap": res["cap"],
        # link_* only: what the model saw and said.
        **({"candidates": [c["name"] for c in res["candidates"]],
            "guess": res["guess"], "reason": res["reason"],
            "triggered": res["triggered"]} if res.get("source") == "link" else {})})
    _PUBLISHER_IDS.append(set(res["ids"]) if res["status"] == "resolved" else set())


def reset_publisher_trace():
    del PUBLISHER_TRACE[:]
    del _PUBLISHER_IDS[:]


def take_publisher_trace():
    out = [dict(e) for e in PUBLISHER_TRACE]
    reset_publisher_trace()
    return out


# ---- Reasoning (bounded ReAct loop) ---------------------------------------
_REASON_HEAD = """You decide whether accumulated evidence is enough to answer a
question about """ + config.PROMPT_REASON_DOMAIN + """ open-data catalog. The catalog only contains what
is shown in the evidence, and it holds no usage statistics such as download or
view counts. Respond ONLY with JSON:
{"action": "answer"} if the evidence is sufficient OR if further searching is
unlikely to surface fundamentally new evidence, or
{"action": "search", "subquery": "..."} with ONE focused follow-up query if a
specific, retrievable piece of evidence is still missing. """

# The default tail bans restatement, which also bans the one move an implied
# phrasing most often calls for: saying the same thing in catalogue wording.
_REASON_TAIL = """Do not just rephrase
the original question. Prefer "answer"."""

_REASON_TAIL_C2 = """If the evidence is thin because the
question is phrased differently from how a catalogue would describe the data,
you may restate it in metadata vocabulary. Prefer "answer" when the evidence
already covers the question."""

# The decision prompt without the ask action and with the default tail.
REASON_SYSTEM = _REASON_HEAD + _REASON_TAIL


# ASK_MODE=on with ASK_STYLE=inline. The third action, offered only in round 1
# and only when the caller can answer it.
_REASON_ASK = """{"action": "ask", "question": "..."} with ONE short clarifying question
to the user, only if the question leaves out a constraint that the evidence
shows matters: the evidence contains clearly different groups of datasets
(for example from different publishers, places or facilities) that could
each be what the user wants, and you cannot tell which one is meant. Do not
ask when the question is already specific enough to answer from the
evidence."""
ASK_PROMPT_SHA256 = hashlib.sha256(_REASON_ASK.encode("utf-8")).hexdigest()

NULL_REPLY = config.NULL_REPLY


def reason_system(ask_allowed=False):
    """The prompt actually used, chosen inside the caller (see rewrite.py)."""
    tail = _REASON_TAIL_C2 if rewrite.loop_restate_allowed() else _REASON_TAIL
    if ask_allowed:
        return _REASON_HEAD + "\n" + _REASON_ASK + "\n" + tail
    return _REASON_HEAD + tail


def evidence_digest(docs, desc_chars=None):
    if desc_chars is None:
        desc_chars = config.DESC_CHARS
    lines = []
    for d in docs:
        tags = ", ".join(d.get("tags", [])[:5])
        tag_part = ", tags=[%s]" % tags if tags else ""
        org = d.get("organization") or "unknown publisher"
        body = (d.get("description") or "")[:desc_chars]
        # HYDRATE_CANDIDATES. With the switch off no document carries
        # graph_note and the relation text sits in the description.
        note = d.get("graph_note")
        if note:
            body = "%s [%s]" % (body, note) if body else note
        lines.append("[%s] (%s, published by %s%s) %s -- %s" % (
            d["id"][:8], d["type"], org, tag_part,
            d["title"], body,
        ))
    if not lines:
        return "(no evidence yet)"
    return "\n".join(lines)


def evidence_titles(docs):
    lines = []
    for d in docs:
        org = d.get("organization") or "unknown publisher"
        description = d.get("description") or ""
        note = ""
        # graph_note exists only with HYDRATE_CANDIDATES on; off, the
        # relation is recognised in the description as before.
        if d.get("graph_note"):
            note = " -- %s" % d["graph_note"][:140]
        elif description.startswith("graph evidence:"):
            note = " -- %s" % description[:140]
        lines.append("- [%s] %s (%s, published by %s)%s"
                     % (d["id"][:8], d["title"], d["type"], org, note))
    if not lines:
        return "(no evidence)"
    return "\n".join(lines)


# Counts "ask" decisions that were not accepted: returned while asking was not
# allowed, or without a question.
ASK_TRACE = {"fallbacks": 0}


def reset_ask_trace():
    ASK_TRACE["fallbacks"] = 0


def take_ask_trace():
    out = dict(ASK_TRACE)
    reset_ask_trace()
    return out


def decide(question, docs, ask_allowed=False):
    out = call_llm(
        reason_system(ask_allowed),
        "Question: %s\n\nEvidence so far:\n%s"
        % (question, evidence_digest(docs[:config.JUDGE_DOCS])),
    )
    decision = parse_json(out, {"action": "answer"})
    if decision.get("action") == "ask" and not (
            ask_allowed and str(decision.get("question") or "").strip()):
        ASK_TRACE["fallbacks"] += 1
    return decision


# ASK_STYLE=separate. A call of its own that only decides whether to ask;
# decide() keeps its original prompt.
_CLARIFY_SYSTEM = """You help a user search an open-data catalog. You see the user's search
request and the first results the search returned. Decide whether to ask the
user ONE short clarifying question before going on.
Ask only if both hold: (1) the request leaves out a constraint that matters,
and (2) the results contain clearly different groups of datasets (for example
from different publishers, places or facilities) that could each be what the
user wants.
If the request already says what it needs, do not ask.
Respond ONLY with JSON: {"ask": true, "question": "..."} or {"ask": false}"""
CLARIFY_PROMPT_SHA256 = hashlib.sha256(
    _CLARIFY_SYSTEM.encode("utf-8")).hexdigest()

# Unparseable clarify() output, or "ask": true without a question; both are
# treated as "do not ask".
CLARIFY_TRACE = {"fallbacks": 0}


def reset_clarify_trace():
    CLARIFY_TRACE["fallbacks"] = 0


def take_clarify_trace():
    out = dict(CLARIFY_TRACE)
    reset_clarify_trace()
    return out


def clarify(question, docs):
    """(verdict, raw). verdict is {"ask": bool, "question": str}."""
    raw = call_llm(
        _CLARIFY_SYSTEM,
        "Question: %s\n\nEvidence so far:\n%s"
        % (question, evidence_digest(docs[:config.JUDGE_DOCS])),
    )
    parsed = parse_json(raw, None)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("ask"), bool):
        CLARIFY_TRACE["fallbacks"] += 1
        return {"ask": False, "question": ""}, raw
    agent_question = str(parsed.get("question") or "").strip()
    if parsed["ask"] and not agent_question:
        CLARIFY_TRACE["fallbacks"] += 1
        return {"ask": False, "question": ""}, raw
    return {"ask": parsed["ask"], "question": agent_question}, raw


# ASK_STYLE=slot: a clarifying question decided by a rule, not by the model.
SLOT_ASK_TEMPLATE = ("Which area or organisation are you interested in? "
                     "For example: %s, %s or %s.")
SLOT_MIN_PUBLISHERS = 3


def slot_clarify(question, plan, docs):
    """(verdict, raw) like clarify(): verdict is {"ask": bool, "question": str}.
    No model call. `raw` is a fixed JSON string describing the rule's inputs."""
    place = str((plan or {}).get("place") or "").strip()
    entity = str((plan or {}).get("entity") or "").strip()
    publishers = []
    for d in docs[:config.JUDGE_DOCS]:
        name = str(d.get("organization") or "").strip()
        if name and name not in publishers:
            publishers.append(name)
    intent = str((plan or {}).get("graph_intent") or "").strip()
    ask = (not place and (not intent or not entity)
           and len(publishers) >= SLOT_MIN_PUBLISHERS)
    raw = json.dumps({"rule": "slot", "place": bool(place),
                      "entity": bool(entity), "publishers": len(publishers),
                      "ask": ask})
    if not ask:
        return {"ask": False, "question": ""}, raw
    return {"ask": True,
            "question": SLOT_ASK_TEMPLATE % tuple(publishers[:3])}, raw


# ASK_STYLE=grounded, the clarification rule of the evaluated configuration.
GROUNDED_ASK_TEMPLATE = ("Which organisation or area do you mean? "
                         "Pick one below or type your own.")
GROUNDED_TITLE_MAX = 10        # title_grounded: the phrase is in 1..10 titles
GROUNDED_TOP_SHARE_MAX = 0.5   # ask only if no publisher has half the head
GROUNDED_OPTIONS_MAX = 4
GROUNDED_OPTION_DOCS = 20      # options come from the first 20 of the pool
_WORDS_RE = re.compile(r"\w+", re.UNICODE)


class ClarifyQuestion(str):
    """A clarifying question that carries answer options. It is a str equal
    to the question text, so every ask_fn that takes the text works as
    before; one that knows about options reads .options, a list of
    {"label", "value"}. Only grounded_clarify() makes one."""

    def __new__(cls, text, options=()):
        obj = super().__new__(cls, text)
        obj.options = [dict(o) for o in options]
        return obj

    def __reduce__(self):
        # copy.deepcopy (ui/observe.py copies verdicts) keeps the options.
        return (ClarifyQuestion, (str(self), self.options))


def grounded_features(plan, docs):
    """The inputs of the grounded rule, all read-only."""
    plan = plan or {}
    place = str(plan.get("place") or "").strip()
    place_resolved = bool(place) and \
        retrieval.normalise_place(place) in retrieval.BBOXES
    entity = str(plan.get("entity") or "").strip()
    family, cap, title_hits = [], None, None
    if not entity:
        status = "none"
    else:
        family, cap = retrieval.organisation_family(entity)
        if 1 <= len(family) <= cap:
            status = "org_grounded"
        elif len(family) > cap:
            status = "org_ambiguous"
        else:
            words = _WORDS_RE.findall(entity.lower())
            status = "none"
            if len(words) >= 2:
                title_hits = retrieval.title_phrase_count(" ".join(words))
                if 1 <= title_hits <= GROUNDED_TITLE_MAX:
                    status = "title_grounded"
                elif title_hits > GROUNDED_TITLE_MAX:
                    status = "title_ambiguous"
    head = docs[:config.JUDGE_DOCS]
    counts = {}
    for d in head:
        name = str(d.get("organization") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    n_pub = len(counts)
    top_share = (max(counts.values()) / float(len(head))) if counts else 0.0
    return {"place": place, "entity": entity,
            "place_resolved": place_resolved, "entity_status": status,
            "family_size": len(family) if entity else None, "cap": cap,
            "title_hits": title_hits, "n_pub": n_pub, "top_share": top_share,
            "family": family}


def grounded_vetoes(features):
    """The conditions that stop the question, in the rule's order; asking
    happens exactly when the list is empty."""
    out = []
    if features["place_resolved"]:
        out.append("place_resolved")
    if features["entity_status"] in ("org_grounded", "title_grounded"):
        out.append("entity_grounded")
    if features["n_pub"] < SLOT_MIN_PUBLISHERS:
        out.append("few_publishers")
    if not features["top_share"] < GROUNDED_TOP_SHARE_MAX:
        out.append("dominant_publisher")
    return out


def grounded_options(docs, features):
    """At most GROUNDED_OPTIONS_MAX publishers of docs[:GROUNDED_OPTION_DOCS],
    most results first, ties by earliest rank. For an ambiguous publisher
    word the members of its family that occur come first."""
    count, first = {}, {}
    for i, d in enumerate(docs[:GROUNDED_OPTION_DOCS]):
        name = str(d.get("organization") or "").strip()
        if not name:
            continue
        count[name] = count.get(name, 0) + 1
        first.setdefault(name, i)
    order = sorted(count, key=lambda n: (-count[n], first[n]))
    if features["entity_status"] == "org_ambiguous":
        family = set(features["family"])
        order = [n for n in order if n in family] + \
            [n for n in order if n not in family]
    return [{"label": "%s (%d result%s)" % (n, count[n],
                                            "" if count[n] == 1 else "s"),
             "value": n} for n in order[:GROUNDED_OPTIONS_MAX]]


def grounded_clarify(question, plan, docs):
    """(verdict, raw) like slot_clarify(); verdict also carries options and
    features. No model call. `raw` is the rule's inputs as JSON."""
    features = grounded_features(plan, docs)
    vetoes = grounded_vetoes(features)
    ask = not vetoes
    options = grounded_options(docs, features)
    public = {k: v for k, v in features.items() if k != "family"}
    raw = json.dumps({"rule": "grounded",
                      "place": features["place"], "entity": features["entity"],
                      "place_resolved": features["place_resolved"],
                      "entity_status": features["entity_status"],
                      "family_size": features["family_size"],
                      "cap": features["cap"],
                      "title_hits": features["title_hits"],
                      "n_pub": features["n_pub"],
                      "top_share": round(features["top_share"], 4),
                      "vetoes": vetoes, "ask": ask, "options": options})
    verdict = {"ask": ask, "question": "", "options": options,
               "features": dict(public, vetoes=vetoes)}
    if ask:
        verdict["question"] = ClarifyQuestion(GROUNDED_ASK_TEMPLATE, options)
    return verdict, raw


def informative(reply):
    """A reply restarts the run unless it is empty or the null reply."""
    text = (reply or "").strip()
    return bool(text) and text != NULL_REPLY


# ---- Generation -----------------------------------------------------------
GEN_SYSTEM = "You answer questions about " + config.PROMPT_GEN_DOMAIN \
    + """ open-data catalog using
ONLY the provided evidence. Each evidence line begins with an eight-character id
in square brackets. To cite an element, copy that id in square brackets
immediately after the element's title, with no parentheses or angle brackets
around it. Never write an id that does not appear in the evidence, and never
attach an id to a different element than the one it labels.
Write your own sentences. Do not copy an evidence line verbatim and do not
reproduce the "--" separator or the abstract text that follows it.
If a geographic area is given, prefer elements covering that area, and say
clearly when an element covers a different or broader region.
If the evidence answers the question only partly, give the partial answer and
say honestly what the catalog does not contain. Only if NOTHING in the evidence
relates to the question, say exactly:
"I don't have enough information in the indexed catalog to answer this."
Never invent dataset names, publishers, or dates. Be concise. When you name a
dataset, copy its title exactly as it appears in the evidence. Do not reword,
abbreviate, or add punctuation to titles."""


def generate(question, docs, place="", feedback="", hint="", temperature=0.2):
    prompt = "Question: %s\n" % question
    if place:
        prompt += "Geographic constraint: %s\n" % place
    if hint:
        prompt += "%s\n" % hint
    if feedback:
        prompt += ("\nYour previous answer was rejected because %s.\n"
                   "Write a better answer.\n" % feedback)
    prompt += "\nEvidence:\n%s" % evidence_digest(docs[:config.GEN_DOCS])
    return call_llm(GEN_SYSTEM, prompt, temperature=temperature)


# ---- Validation (hallucination check + relevance check) -------------------
def read_verdict(raw):
    m = re.search(r"\b(YES|NO)\b", raw.upper())
    return m is not None and m.group(1) == "YES"


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


CITATION_RE = re.compile(r"(?<![0-9a-zA-Z])([0-9a-f]{8})(?![0-9a-zA-Z])")


def _cited_ids(text):
    return [m.group(1) for m in CITATION_RE.finditer(text)
            if re.search(r"[a-f]", m.group(1))]


PUBLISHER_RE = re.compile(r"published by ([^,.;:\n()\[\]]{3,60})", re.I)


# CITE_CHECK=lenient.
CITE_MIN_SHARED = 2           # shared content words (text comparisons)
CITE_MIN_DIGIT_LEN = 3        # "2023", "4113"; not "29" or "9"
CITE_MIN_UNIQUE_LEN = 4
CITE_MAX_WORD_DIFF = 2        # rule 2: words in one side only, both sides
CITE_MIN_COVER = 0.5          # rule 3: share of the shorter side found in the longer
CITE_STOPWORDS = frozenset(config.LANGUAGE.FUNCTION_WORDS) | frozenset((
    "which", "was", "were", "has", "have", "had", "it", "its", "not", "no",
    "all", "any", "some", "such", "can", "may", "will", "would", "there",
    "these", "those", "than", "then", "also", "other", "id", "ids", "who",
    "where", "when", "each", "both", "only", "more", "most", "into", "their",
    "catalog", "catalogue", "catalogs", "information", "element", "elements",
    "record", "records", "includes", "include", "including", "contains",
    "contain", "specifically", "various"))
_CITE_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Read-only record of what the lenient rule let through, for the UI.
CITE_TRACE = []


def reset_cite_trace():
    del CITE_TRACE[:]


def take_cite_trace():
    out = list(CITE_TRACE)
    del CITE_TRACE[:]
    return out


def _content_words(text):
    return {t for t in _CITE_TOKEN_RE.findall((text or "").lower())
            if t not in CITE_STOPWORDS}


def _rough_norm(text):
    """Lower case; a leading the/a/an and anything in parentheses removed;
    punctuation removed; spaces squeezed. The same on both sides."""
    t = (text or "").lower().strip()
    t = re.sub(r"^(?:the|a|an)\s+", "", t)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return re.sub(r"^(?:the|a|an)\s+", "", t)


def _ordered_words(norm):
    return [t for t in _CITE_TOKEN_RE.findall(norm) if t not in CITE_STOPWORDS]


def _distinctive_words(shared, other_texts):
    """Shared words that single the dataset out: a digit and long enough, or
    long enough and in none of `other_texts`."""
    elsewhere = set()
    for text in other_texts or ():
        elsewhere |= _content_words(text)
    return sorted(
        t for t in shared
        if (any(ch.isdigit() for ch in t) and len(t) >= CITE_MIN_DIGIT_LEN)
        or (other_texts is not None and t not in elsewhere
            and len(t) >= CITE_MIN_UNIQUE_LEN))


def roughly_matches(a, b, other_texts=None, min_shared=None):
    """(matches, rule, shared_words) for two pieces of text."""
    na, nb = _rough_norm(a), _rough_norm(b)
    wa, wb = _ordered_words(na), _ordered_words(nb)
    sa, sb = set(wa), set(wb)
    shared = sorted(sa & sb)
    if not na or not nb:
        return False, 0, shared
    # Rule 1 needs a content word in the contained side: "and" is inside
    # almost every text.
    if (na in nb and wa) or (nb in na and wb):
        return True, 1, shared
    # Rules 2 and 3 need at least CITE_MIN_SHARED shared words, so that one
    # coincident word never makes two texts "close".
    if sa and sb and len(shared) >= CITE_MIN_SHARED \
            and len(sa ^ sb) <= CITE_MAX_WORD_DIFF:
        return True, 2, shared
    if sa and sb and len(shared) >= CITE_MIN_SHARED:
        short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
        if len(short & long_) / float(len(short)) >= CITE_MIN_COVER:
            return True, 3, shared
    for single, other in ((na, wb), (nb, wa)):
        if " " not in single and len(single) >= 2 and len(other) >= 2 \
                and single == "".join(w[0] for w in other):
            return True, 4, shared
    if _distinctive_words(shared, other_texts):
        return True, 5, shared
    if min_shared is not None and len(shared) >= min_shared:
        return True, 6, shared
    return False, 0, shared


def _lenient_accepts(cid, line, start, end, prev_end, next_start, allowed,
                     docs):
    """True when the text beside the id at line[start:end] roughly matches the
    cited dataset (title first, then description, then both together; the
    last with the step 5 shared-word rule). The text is what stands between
    the previous citation on the line (or the line's start) and this id; if
    that holds no content word, the line up to this id; if still none, the
    text after the id. Records the acceptance in CITE_TRACE."""
    doc = allowed[cid]
    beside = line[prev_end:start]
    if not _content_words(beside):
        beside = line[:start]
    if not _content_words(beside):
        beside = line[end:next_start]
    title = doc.get("title", "") or ""
    description = doc.get("description", "") or ""
    others = ["%s %s" % (o.get("title", ""), o.get("description", ""))
              for o in docs if o is not doc]
    for kind, target in (("title", title), ("description", description),
                         ("description", "%s %s" % (title, description))):
        ok, rule, shared = roughly_matches(
            beside, target, other_texts=others,
            min_shared=CITE_MIN_SHARED if target is not title else None)
        if ok:
            CITE_TRACE.append({
                "id": cid, "type": kind, "rule": rule, "shared": shared,
                "distinctive": _distinctive_words(set(shared), others)})
            return True
    return False


def citation_problems(answer, docs):
    allowed = {d["id"][:8].lower(): d for d in docs}
    problems = []
    lenient = getattr(config, "CITE_CHECK", "strict") == "lenient"
    for line in answer.splitlines():
        ids_here = [c for c in _cited_ids(line) if c in allowed]
        spans = [(m.start(), m.end()) for m in CITATION_RE.finditer(line)
                 if re.search(r"[a-f]", m.group(1))]
        for k, cid in enumerate(_cited_ids(line)):
            if cid not in allowed:
                problems.append("the id %s is not in the evidence" % cid)
                continue
            title = _norm(allowed[cid].get("title", ""))
            if title and title[:40] not in _norm(line):
                if lenient and _lenient_accepts(
                        cid, line, spans[k][0], spans[k][1],
                        spans[k - 1][1] if k else 0,
                        spans[k + 1][0] if k + 1 < len(spans) else len(line),
                        allowed, docs):
                    continue
                problems.append("the id %s belongs to '%s', not to the element "
                                "named beside it"
                                % (cid, allowed[cid].get("title", "")[:60]))
        if ids_here:
            orgs = [_norm(allowed[c].get("organization", "")) for c in ids_here]
            orgs = [o for o in orgs if o]
            for match in PUBLISHER_RE.finditer(line):
                claimed = _norm(match.group(1))
                if not claimed or not orgs:
                    continue
                if not any(claimed in o or o in claimed for o in orgs):
                    if lenient:
                        hit = None
                        for c in ids_here:
                            ok, rule, shared = roughly_matches(
                                match.group(1),
                                allowed[c].get("organization", ""))
                            if ok:
                                hit = (c, rule, shared)
                                break
                        if hit:
                            CITE_TRACE.append({
                                "id": hit[0], "type": "publisher",
                                "rule": hit[1], "shared": hit[2],
                                "distinctive": []})
                            continue
                    problems.append("it says the element is published by '%s', "
                                    "but the evidence says '%s'"
                                    % (match.group(1).strip()[:40],
                                       allowed[ids_here[0]].get(
                                           "organization", "")[:40]))

    if not [c for c in _cited_ids(answer) if c in allowed]:
        normalised = _norm(answer)
        named = [t for t in (d.get("title") or "" for d in docs)
                 if t and _norm(t)[:40] in normalised]
        if named:
            problems.append("it names '%s' but cites no evidence id at all; put "
                            "the eight-character id in square brackets straight "
                            "after each title" % named[0][:60])
    return problems


GROUNDING_SYSTEM = (
    "You check whether an ANSWER invents datasets that are not in the catalog. "
    "The EVIDENCE lists every element the answer is allowed to mention. Each "
    "entry starts with an eight-character id in square brackets, and an id in "
    "the ANSWER that matches one of those is a citation, not the name of a "
    "separate dataset. Reply with YES or NO on the first line, then one short "
    "sentence of reasoning.\n"
    "Judge names only. Whether a stated relationship, date, publisher or "
    "description is accurate is NOT your task; an answer that describes how two "
    "listed elements relate is still YES.\n"
    "YES: every dataset the ANSWER names appears in the EVIDENCE list. Wording "
    "does not have to match exactly, and describing what a dataset contains is "
    "fine. Saying there is not enough information is also YES.\n"
    "NO: the ANSWER names a dataset that is not in the EVIDENCE list."
)

RELEVANCE_SYSTEM = (
    "Answer strictly YES or NO: does the ANSWER address the QUESTION? Say YES "
    "if it answers fully or partly, or if it honestly explains what the catalog "
    "does and does not contain. Say NO only if the answer is off-topic."
)


def validate(question, answer, docs):
    judge_docs = docs[:config.JUDGE_DOCS]
    problems = citation_problems(answer, judge_docs)

    raw_llm = call_llm(GROUNDING_SYSTEM,
                       "EVIDENCE:\n%s\n\nANSWER:\n%s"
                       % (evidence_titles(judge_docs), answer))
    llm_grounded = read_verdict(raw_llm)

    raw_r = call_llm(RELEVANCE_SYSTEM,
                     "QUESTION: %s\n\nANSWER: %s" % (question, answer))

    grounded = not problems
    if config.GROUNDING_LLM_BLOCKS and not llm_grounded:
        grounded = False

    if problems:
        raw_g = "NO (citation check): %s" % "; ".join(problems)
    else:
        raw_g = "YES (citation check) | llm advisory: %s" % (
            "YES" if llm_grounded else "NO -- %s" % raw_llm.replace("\n", " ")[:120])
    return grounded, read_verdict(raw_r), raw_g, raw_r


# ---- Full pipeline --------------------------------------------------------
def _too_similar(a, b):
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) / float(len(ta | tb)) >= 0.8


# Fields that make a document a member of the graph voice (rerank.py). They
# are written only under RERANK_MODE=vote.
_VOICE_FIELDS = ("graph_relation", "graph_weight")


def voice_round_filter(fused, round_index):
    """GRAPH_VOICE_ROUNDS=first: documents fused in a round after the first
    lose their graph-voice fields, so they cannot become voice members. They
    stay in the candidate pool with everything else (graph_note included, so
    decide() and the generator read the same evidence), and the cross-encoder
    still ranks them. Copies are returned; nothing shared is mutated.
    """
    mode = getattr(config, "GRAPH_VOICE_ROUNDS", "all")
    if mode not in ("all", "first"):
        raise ValueError("GRAPH_VOICE_ROUNDS must be all or first, got %r" % mode)
    if mode == "all" or round_index == 0:
        return fused
    return [{k: v for k, v in doc.items() if k not in _VOICE_FIELDS}
            for doc in fused]


def _ask_exchange(agent_question, ask_fn, question, history):
    """One clarifying exchange, shared by both ASK_STYLEs. Yields ask and
    ask_reply; on an informative reply also ask_restart and the whole run on
    q' = question + "; " + reply (asking off). Returns True if it restarted."""
    yield "ask", agent_question
    reply = ask_fn(agent_question)
    yield "ask_reply", reply
    if not informative(reply):
        return False
    q_prime = question + "; " + reply
    yield "ask_restart", q_prime
    yield from run(q_prime, history, ask_fn=None, _ask_budget=0)
    return True


# EVAL_RECORD_POOL=1. What run() handed to finalise() and the methods each
# round was routed to; read and reset by eval/run_eval.py.
POOL_TRACE = {"pool_ids": None, "round_methods": []}


def reset_pool_trace():
    POOL_TRACE["pool_ids"] = None
    POOL_TRACE["round_methods"] = []


def take_pool_trace():
    out = {"pool_ids": POOL_TRACE["pool_ids"],
           "round_methods": list(POOL_TRACE["round_methods"])}
    reset_pool_trace()
    return out


def resolve_citations(answer, docs):
    """Read-only: the evidence ids an answer cites."""
    by_prefix = {}
    for d in docs:
        by_prefix.setdefault(d["id"][:8].lower(), []).append(d["id"])
    cited, collisions, unresolved = [], [], []
    for cid in _cited_ids(answer or ""):
        ids = by_prefix.get(cid.lower())
        if not ids:
            if cid not in unresolved:
                unresolved.append(cid)
        elif len(ids) > 1:
            if cid not in [c["prefix"] for c in collisions]:
                collisions.append({"prefix": cid, "ids": list(ids)})
        elif ids[0] not in cited:
            cited.append(ids[0])
    return cited, collisions, unresolved


def run(question, history=None, ask_fn=None, _ask_budget=None):
    """The pipeline as a stream of (stage, message) pairs."""
    history = history or []
    # Publisher linking: the resolutions of this run only (a restart is a new run).
    if getattr(config, "ENTITY_RESOLVE", "off") != "off":
        reset_publisher_trace()
    ask_on = getattr(config, "ASK_MODE", "off") == "on" and ask_fn is not None
    ask_budget = getattr(config, "ASK_MAX", 1) if _ask_budget is None \
        else _ask_budget
    asks_used = 0

    yield "memory", "checking conversational context"
    q = memory_rewrite(question, history)
    if q != question:
        yield "memory", "rewritten: %s" % q

    yield "routing", "selecting retrieval methods"
    plan = route(q)
    place = plan.get("place", "")
    intent = plan.get("graph_intent", "")
    entity = plan.get("entity", "")
    yield "routing", json.dumps(plan, ensure_ascii=False)

    seen = set()
    evidence = []
    rounds = {}
    current_q = q
    asked = [q]
    for i in range(config.MAX_ITERATIONS):
        yield "retrieve", "iteration %d: %s" % (i + 1, current_q)
        fused = voice_round_filter(retrieval.rrf_fuse(retrieve(current_q, plan)), i)
        rounds["round%d" % (i + 1)] = fused
        if getattr(config, "EVAL_RECORD_POOL", False):
            POOL_TRACE["round_methods"].append(list(plan.get("methods") or []))

        # The saturation guard below asks "did this round's top ten bring
        # anything new".
        added = 0
        head_ids = {d["id"] for d in fused[:config.TOP_K]}
        for doc in fused:
            if doc["id"] not in seen:
                seen.add(doc["id"])
                evidence.append(doc)
                if doc["id"] in head_ids:
                    added += 1
        yield "rerank", "evidence pool: %d elements (+%d in the head)" % (
            len(evidence), added)

        if i > 0 and added <= 2:
            yield "reasoning", "evidence pool saturated, proceeding to answer"
            break

        if ask_on and i == 0 and asks_used < ask_budget \
                and getattr(config, "ASK_STYLE", "inline") in ("separate",
                                                               "slot",
                                                               "grounded"):
            if config.ASK_STYLE == "slot":
                verdict, raw = slot_clarify(q, plan, evidence)
            elif config.ASK_STYLE == "grounded":
                verdict, raw = grounded_clarify(q, plan, evidence)
            else:
                verdict, raw = clarify(q, evidence)
            yield "clarify", raw
            if verdict["ask"]:
                asks_used += 1
                restarted = yield from _ask_exchange(
                    verdict["question"], ask_fn, question, history)
                if restarted:
                    return
            # Asked with a null reply, or not asked: decide() as without asking.
            decision = decide(q, evidence)
        elif ask_on and i == 0 and asks_used < ask_budget:
            decision = decide(q, evidence, ask_allowed=True)
            agent_question = str(decision.get("question") or "").strip()
            if decision.get("action") == "ask" and agent_question:
                asks_used += 1
                restarted = yield from _ask_exchange(
                    agent_question, ask_fn, question, history)
                if restarted:
                    return
                # Nothing to add: the same round decides again, with the
                # ordinary prompt.
                decision = decide(q, evidence)
        else:
            decision = decide(q, evidence)
        if decision.get("action") != "search" or not decision.get("subquery"):
            break
        subquery = decision["subquery"]
        if any(_too_similar(subquery, earlier) for earlier in asked):
            yield "reasoning", "follow-up repeats an earlier query, stopping"
            break
        asked.append(subquery)
        current_q = subquery
        new_plan = route(current_q)
        new_plan["place"] = new_plan.get("place") or place
        plan = new_plan
        yield "reasoning", "needs more evidence -> %s" % current_q

    if len(rounds) > 1:
        evidence = retrieval.rrf_fuse(rounds)

    # Publisher voice (voice, both, link_both): a record of a publisher
    # resolved in ANY round of this run is marked, also when it was fused in a
    # round that resolved nothing.
    if getattr(config, "ENTITY_RESOLVE", "off") in ("voice", "both", "link_both"):
        resolved = set().union(*_PUBLISHER_IDS) if _PUBLISHER_IDS else set()
        for doc in evidence:
            if doc["id"] in resolved:
                doc["publisher_match"] = True

    # The cut to the ten documents that are scored, cited and shown happens in
    # exactly one place, and the reranker sits in front of it.
    if getattr(config, "EVAL_RECORD_POOL", False):
        POOL_TRACE["pool_ids"] = [d["id"] for d in evidence]
    evidence = retrieval.finalise(question, evidence, top_k=config.TOP_K)

    yield "evidence_ids", json.dumps([d["id"] for d in evidence[:config.TOP_K]])

    yield from answer_stage(q, evidence, place, intent, entity)


def answer_stage(q, evidence, place, intent, entity):
    """Generation, validation and regeneration over the final evidence, as a
    stream of (stage, message) pairs ending with "answer" and "references".
    """
    hint = ""
    # The hint reads no document field: it tells the generator to report the
    # relation each evidence line states.
    if intent in ("PROVENANCE", "CO_CONTRIBUTION") and entity:
        hint = ("This question asks which OTHER elements relate to '%s'. Do not "
                "answer with that element itself. List the elements whose "
                "evidence line reports a shared-tag edge or a shared "
                "contributor with it, and state that relationship." % entity)

    yield "generation", "synthesizing grounded answer"
    answer = generate(q, evidence, place=place, hint=hint)

    accepted = False
    for attempt in range(config.MAX_REGENERATIONS + 1):
        grounded, relevant, raw_g, raw_r = validate(q, answer, evidence)
        yield "validation", "grounded=%s relevant=%s" % (grounded, relevant)
        if grounded and relevant:
            accepted = True
            break
        yield "debug", "rejected: %r | judge_g=%r | judge_r=%r" % (
            answer[:150], raw_g[:200], raw_r[:80])
        if attempt < config.MAX_REGENERATIONS:
            reasons = []
            if not grounded:
                if raw_g.startswith("NO (citation check)"):
                    reasons.append(raw_g.split(": ", 1)[-1])
                else:
                    reasons.append("it made claims the evidence does not support")
            if not relevant:
                reasons.append("it did not address the question")
            yield "validation", "regeneration attempt %d" % (attempt + 1)
            answer = generate(q, evidence, place=place, hint=hint,
                              feedback=" and ".join(reasons), temperature=0.6)
    if not accepted:
        answer = REFUSAL

    yield "answer", answer
    yield "references", json.dumps(
        [{"id": d["id"], "title": d["title"], "url": d["url"],
          "from_methods": d.get("from_methods", [])}
         for d in evidence[:config.TOP_K]],
        ensure_ascii=False, indent=2,
    )
