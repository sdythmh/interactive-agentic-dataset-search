"""Whole-question rewriting into metadata phrasing (variants c1 and c2)."""
import hashlib
import json
import os
import threading

import numpy as np
from openai import OpenAI

import config

_client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)

# ---- The prompt -----------------------------------------------------------
# PROMPT_SHA256 is printed by run_eval.py so a run can be tied to the exact
# wording that produced it. The words naming the catalogue come from the
# corpus (config.PROMPT_REWRITE_DOMAIN); the line break before them belongs to
# the template.
REWRITE_SYSTEM = """You rewrite a user's question into the vocabulary and form used by
""" + config.PROMPT_REWRITE_DOMAIN + """ open-data catalogue metadata. Output a compact noun phrase
list, not a sentence and not a question.

Rules:
- Every place name, organisation name and proper noun in the question
  MUST appear verbatim in your output.
- Use the wording a data publisher would use in a title, tag or
  organisation field.
- No more than 20 words. No punctuation except commas.
- Output the rewritten query only. No explanation."""

REWRITE_USER = "Question: %s"

PROMPT_SHA256 = hashlib.sha256(
    (REWRITE_SYSTEM + "\n" + REWRITE_USER).encode("utf-8")
).hexdigest()


# ---- Mode switches --------------------------------------------------------
def _mode():
    return getattr(config, "EXPANSION_MODE", "off").lower()


def _c_active():
    """True for the two rewriting variants only."""
    return _mode() in ("c1", "c2")


def loop_restate_allowed():
    """c2 only: lets the ReAct loop restate the question in metadata wording."""
    return _mode() == "c2"


# ---- Corpus vocabulary, token level ---------------------------------------
# Reuses data/vocab_index.npz (vocab + df, not vectors). The npz holds whole
# terms -- tags, organisation names, title n-grams -- so a token-level table is
# derived from it by splitting each term. A token's df is the largest df of any
# term containing it; only df == 0 (i.e. absent) matters for the guard.
# Token pattern of the corpus's language (corpora/<name>/language.py); it
# must match the one ingest/build_token_df.py counted with.
TOKEN_RE = config.LANGUAGE.QUERY_TOKEN_RE

_token_df = None
_lock = threading.Lock()


def corpus_token_df():
    global _token_df
    if _token_df is not None:
        return _token_df
    with _lock:
        if _token_df is not None:
            return _token_df
        path = config.VOCAB_INDEX_FILE
        table = {}
        if not os.path.exists(path):
            print("[warn] rewrite: %s not found, the df==0 grounding guard is "
                  "disabled (run ingest/build_vocabulary.py)" % path)
        else:
            data = np.load(path, allow_pickle=True)
            for term, df in zip(data["vocab"].tolist(), data["df"].tolist()):
                for tok in TOKEN_RE.findall(str(term).lower()):
                    if int(df) > table.get(tok, 0):
                        table[tok] = int(df)
            print("[info] rewrite: grounding vocabulary %d distinct tokens "
                  "from %s" % (len(table), path))
        _token_df = table
    return _token_df


def _ground(text, question):
    """Drop every token that is neither in the question nor in the corpus."""
    table = corpus_token_df()
    q_tokens = {t.lower() for t in TOKEN_RE.findall(question)}
    kept, dropped = [], []
    for chunk in text.split():
        tokens = TOKEN_RE.findall(chunk)
        if not tokens:
            continue
        token = tokens[0]
        low = token.lower()
        if q_tokens and low in q_tokens:
            keep = True
        elif not table:                      # no vocabulary -> guard disabled
            keep = True
        else:
            keep = low in table
        if keep:
            kept.append(token + ("," if chunk.rstrip().endswith(",") else ""))
        else:
            dropped.append(token)
    return " ".join(kept).strip(" ,"), dropped


def _guard():
    return getattr(config, "REWRITE_GUARD", "strict").lower()


def _gate():
    return getattr(config, "REWRITE_GATE", "off").lower()


# Closed-class sentence openers. A question that begins with one of these, or
# that ends in a question mark, is a sentence rather than a noun phrase.
SENTENCE_OPENERS = config.LANGUAGE.SENTENCE_OPENERS


def _has_non_latin(text):
    """A question written in another script cannot be matched lexically."""
    return config.LANGUAGE.has_foreign_script(text)


_fulltext_df = None


def corpus_fulltext_df():
    """Token frequencies over the corpus full text (data/token_df.json)."""
    global _fulltext_df
    if _fulltext_df is not None:
        return _fulltext_df
    with _lock:
        if _fulltext_df is not None:
            return _fulltext_df
        path = getattr(config, "TOKEN_DF_FILE", "data/token_df.json")
        if not os.path.exists(path):
            raise SystemExit(
                "the rewrite gate needs %s, which does not exist.\n"
                "Build it with: python ingest/build_token_df.py" % path)
        with open(path) as f:
            payload = json.load(f)
        _fulltext_df = payload["df"]
        print("[info] rewrite: gate vocabulary %d distinct tokens over the "
              "corpus full text (%s)" % (len(_fulltext_df), path))
    return _fulltext_df


def gate_verdict(question):
    """The rewrite gate: should this question be rewritten at all?"""
    if _gate() != "question_type":
        return True, "gate_off"

    if _has_non_latin(question):
        return True, "non_latin"

    tokens = TOKEN_RE.findall(question or "")
    if tokens:
        first = tokens[0].lower()
        if first in SENTENCE_OPENERS or (question or "").rstrip().endswith("?"):
            return True, "interrogative"

    table = corpus_fulltext_df()
    for token in tokens:
        low = token.lower()
        if low in FUNCTION_WORDS:
            continue
        if table.get(low, 0) == 0:
            return True, "unseen:%s" % token
    return False, "canonical"


def _keeps_all(text, must_keep):
    lowered = (text or "").lower()
    return all(k.lower() in lowered for k in must_keep)


# Closed-class function words, defined by the corpus's language module
# (corpora/<name>/language.py).
FUNCTION_WORDS = config.LANGUAGE.FUNCTION_WORDS


def identity_tokens(must_keep):
    """REWRITE_GUARD=token: the tokens of must_keep that actually carry entity identity."""
    table = corpus_token_df()
    required, seen = [], set()
    for phrase in must_keep:
        for token in TOKEN_RE.findall(phrase or ""):
            low = token.lower()
            if low in seen:
                continue
            if token[0].isupper():
                keep = True                      # proper noun
            elif low in FUNCTION_WORDS:
                keep = False                     # never an identity token
            else:
                keep = table.get(low, 0) == 0    # unseen word: a name or typo
            if keep:
                seen.add(low)
                required.append(token)
    return required


def _guard_verdict(text, must_keep):
    """Returns (ok, required_tokens_or_None, missing_tokens)."""
    if _guard() != "token":
        return _keeps_all(text, must_keep), None, []
    required = identity_tokens(must_keep)
    lowered = (text or "").lower()
    missing = [t for t in required if t.lower() not in lowered]
    return (not missing), required, missing


# ---- The LLM call ---------------------------------------------------------
def _call(question):
    extra = {} if config.LLM_SEED is None else {"seed": config.LLM_SEED}
    resp = _client.chat.completions.create(
        model=config.LLM_MODEL,
        temperature=config.LLM_TEMPERATURE,
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": REWRITE_USER % question},
        ],
        **extra,
    )
    return (resp.choices[0].message.content or "").strip()


def _first_line(raw):
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip().strip('"').strip("'").strip()
        if line:
            return line
    return ""


# ---- Trace ----------------------------------------------------------------
# Every rewrite is recorded so the report can say *why* a variant behaved as it
# did, not only by how much it moved. The third stage's value came from
# expansion_a_terms.log, not from the four numbers.
TRACE = []
_cache = {}


def reset_trace():
    del TRACE[:]


def take_trace():
    entries = list(TRACE)
    del TRACE[:]
    return entries


def _rewrite_uncached(question, must_keep):
    entry = {
        "question": question,
        "must_keep": list(must_keep),
        "raw": "",
        "rewritten": question,
        "dropped": [],
        "required": [],
        "status": "fallback:llm_error",
    }
    try:
        raw = _call(question)
    except Exception as exc:                                   # noqa: BLE001
        print("[warn] rewrite: LLM call failed (%r), using original question"
              % exc)
        return entry

    entry["raw"] = raw
    candidate = _first_line(raw)
    if not candidate:
        entry["status"] = "fallback:empty"
        return entry

    grounded, dropped = _ground(candidate, question)
    entry["dropped"] = dropped
    if not grounded:
        entry["status"] = "fallback:empty_after_grounding"
        return entry

    words = grounded.split()
    limit = getattr(config, "REWRITE_MAX_WORDS", 24)
    if len(words) > limit:
        trimmed = " ".join(words[:limit]).strip(" ,")
        # Truncation must not be what breaks the constraint; if it does, keep
        # the untrimmed text and let the guard below judge that.
        if _guard_verdict(trimmed, must_keep)[0]:
            grounded = trimmed

    ok, required, missing = _guard_verdict(grounded, must_keep)
    if required is not None:
        entry["required"] = required
    if not ok:
        entry["status"] = "fallback:must_keep"
        entry["missing"] = missing
        return entry

    entry["rewritten"] = grounded
    # Under the token guard, a rewrite accepted because must_keep held no
    # identity token at all is a different event from one that was actually
    # checked; keeping them apart is what makes the effect attributable.
    if required is not None and not required:
        entry["status"] = "ok:no_identity_token"
    else:
        entry["status"] = "ok"
    return entry


# The gate reports an unseen word as "unseen:<token>", every other reason as a
# bare name.
GATE_FAMILIES = ("interrogative", "unseen", "non_latin", "gate_off", "canonical")


def gate_reason_family(entry):
    """The family of the gate reason recorded in `entry`, or '' if there is
    none. entry may be None. 'unseen:datsets' -> 'unseen'."""
    if not entry:
        return ""
    return (entry.get("gate_reason") or "").split(":", 1)[0]


def outcome_for(question):
    """The latest TRACE entry recorded for `question`, or None."""
    for entry in reversed(TRACE):
        if entry.get("question") == question:
            return entry
    return None


def rewrite(question, must_keep=()):
    """Return the rewritten query, or the original question unchanged."""
    if not _c_active():                    # kill switch, first line, required
        return question

    keep = tuple(k.strip() for k in must_keep if k and str(k).strip())

    # The gate. Decided before the LLM is called: a canonical noun-phrase query is
    # left alone, and costs nothing.
    open_gate, reason = gate_verdict(question)
    if not open_gate:
        entry = {"question": question, "must_keep": list(keep), "raw": "",
                 "rewritten": question, "dropped": [], "required": [],
                 "status": "skipped:canonical"}
        TRACE.append(entry)
        print("[rewrite] %s  ->  (already in metadata form) not rewritten"
              % question)
        return question

    # The guard mode and the gate change the verdict, so both belong in the
    # cache key even though neither changes inside one run.
    key = (question, keep, _guard(), _gate())
    if key not in _cache:
        _cache[key] = _rewrite_uncached(question, keep)
    entry = dict(_cache[key])
    entry["gate_reason"] = reason
    TRACE.append(entry)

    if entry["status"].startswith("ok"):
        note = ""
        if entry["dropped"]:
            note += "  [dropped df==0: %s]" % ", ".join(entry["dropped"])
        if entry["status"] == "ok:no_identity_token":
            note += "  [no identity token to protect]"
        elif entry.get("required"):
            note += "  [kept: %s]" % ", ".join(entry["required"])
        if reason not in ("gate_off",):
            note += "  [gate: %s]" % reason
        print("[rewrite] %s  ->  %s%s" % (question, entry["rewritten"], note))
    else:
        note = ""
        if entry.get("missing"):
            note = "  [missing: %s]" % ", ".join(entry["missing"])
        print("[rewrite] %s  ->  (%s) original kept%s"
              % (question, entry["status"], note))
    return entry["rewritten"]


__all__ = ["rewrite", "loop_restate_allowed", "reset_trace", "take_trace",
           "outcome_for",
           "identity_tokens", "corpus_token_df", "gate_verdict",
           "PROMPT_SHA256", "REWRITE_SYSTEM", "TRACE"]
