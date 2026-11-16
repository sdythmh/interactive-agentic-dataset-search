"""HYDE switch: a hypothetical document on the query side of the vector path."""
import hashlib
import json
import os

from openai import OpenAI

import config
import rewrite

llm = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)

# Written independently for this project; the domain phrase is the corpus
# fragment every other prompt uses.
HYDE_SYSTEM = (
    "Write one short, invented catalogue record for a dataset that would "
    "answer the user's question, as such a record would read in "
    + config.PROMPT_GEN_DOMAIN + " open-data catalogue.\n"
    "Output exactly three lines and nothing else:\n"
    "Title: <one line, the way a publishing agency would name the dataset>\n"
    "Description: <one or two sentences of factual metadata prose: what the "
    "dataset contains, at what level of detail, for which area and period>\n"
    "Tags: <three to six short keywords, comma separated>\n"
    "Invent plausible agency names, areas and years where the question does "
    "not give them. Do not answer the question, do not explain, do not "
    "mention that the record is hypothetical."
)
PROMPT_SHA = hashlib.sha256(HYDE_SYSTEM.encode()).hexdigest()

GATE_REASONS = ("interrogative", "unseen", "non_latin")

TRACE = []
_cache = None


def active():
    return bool(getattr(config, "HYDE", False))


def _when():
    return getattr(config, "HYDE_WHEN", "gated").lower()


def gate_reason(question):
    """Why HyDE would fire for this question, or '' if it would not."""
    if not active():
        return ""
    if _when() == "always":
        return "always"
    if _when() != "gated":
        raise ValueError("HYDE_WHEN must be gated or always, got %r" % _when())
    # rewrite.gate_reason_family() turns "unseen:<token>" into "unseen";
    # "gate_off" and "canonical" are not the gate opening on question type and
    # do not fire.
    reason = rewrite.gate_reason_family(rewrite.outcome_for(question))
    return reason if reason in GATE_REASONS else ""


def _cache_path():
    return getattr(config, "HYDE_CACHE_FILE", os.path.join("cache", "hyde_cache.jsonl"))


def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    path = _cache_path()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    _cache[row["key"]] = row["text"]
    return _cache


def _cache_key(question):
    digest = hashlib.sha256()
    digest.update(("%s\n%s\n%s" % (config.LLM_MODEL, PROMPT_SHA, question)).encode())
    return digest.hexdigest()


def _append_cache(key, question, text):
    path = _cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({"key": key, "model": config.LLM_MODEL,
                            "prompt_sha256": PROMPT_SHA, "question": question,
                            "text": text}, ensure_ascii=False) + "\n")


def generate(question):
    """(text, cache_hit). Empty text when the model returns nothing."""
    key = _cache_key(question)
    cache = _load_cache()
    if key in cache:
        return cache[key], True
    extra = {} if config.LLM_SEED is None else {"seed": config.LLM_SEED}
    resp = llm.chat.completions.create(
        model=config.LLM_MODEL,
        temperature=0.0,
        messages=[{"role": "system", "content": HYDE_SYSTEM},
                  {"role": "user", "content": question}],
        **extra,
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        print("[warn] hyde: empty response for %r" % question[:60])
        return "", False
    cache[key] = text
    _append_cache(key, question, text)
    return text, False


def document_text(raw):
    """The generated lines as one document-shaped string."""
    title = description = tags = ""
    for line in raw.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("title:"):
            title = line.split(":", 1)[1].strip()
        elif low.startswith("description:"):
            description = line.split(":", 1)[1].strip()
        elif low.startswith("tags:"):
            tags = line.split(":", 1)[1].strip()
    if not (title or description or tags):
        return " ".join(raw.split())
    parts = []
    if title:
        parts.append(title.rstrip(".") + ".")
    if tags:
        parts.append("Tags: %s." % tags)
    if description:
        parts.append(description)
    return " ".join(parts)


def query_vector(question, lexical_query, encode):
    """The vector the semantic path should use, or None to keep its own."""
    reason = gate_reason(question)
    entry = {"fired": False, "reason": reason, "cache_hit": None,
             "sha256": None, "chars": 0}
    if not reason:
        TRACE.append(entry)
        return None
    raw, hit = generate(question)
    entry["cache_hit"] = hit
    if not raw:
        entry["error"] = "empty"
        TRACE.append(entry)
        return None
    text = document_text(raw)
    entry.update(fired=True, sha256=hashlib.sha256(raw.encode()).hexdigest()[:16],
                 chars=len(text))
    TRACE.append(entry)

    query_text = lexical_query
    if getattr(config, "EMBED_QUERY_PREFIX", False):
        query_text = config.BGE_QUERY_PREFIX + lexical_query
    q = encode(query_text)
    # The hypothetical record is a document, so it is encoded bare: the BGE
    # instruction belongs on the query side only.
    d = encode(text)
    mixed = [(a + b) / 2.0 for a, b in zip(q, d)]
    norm = sum(x * x for x in mixed) ** 0.5 or 1.0
    return [x / norm for x in mixed]


def reset_trace():
    del TRACE[:]


def take_trace():
    """Per-row summary: how often it fired, from cache, and the texts used."""
    fired = [e for e in TRACE if e["fired"]]
    out = {"calls": len(TRACE), "fired": len(fired),
           "cache_hits": sum(1 for e in TRACE if e.get("cache_hit")),
           "reasons": sorted({e["reason"] for e in TRACE if e["reason"]}),
           "sha256": [e["sha256"] for e in fired],
           "errors": sum(1 for e in TRACE if e.get("error"))}
    reset_trace()
    return out


def fingerprint():
    """Recorded only when the switch is on, so every earlier record stands."""
    if not active():
        return None
    return {"when": _when(), "model": config.LLM_MODEL,
            "prompt_sha256": PROMPT_SHA, "mix": "mean+l2norm",
            "cache": _cache_path()}
