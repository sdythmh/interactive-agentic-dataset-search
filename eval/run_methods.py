"""Two more configurations for eval/run_eval.py: the dense baselines.

Usage:
    bash eval/run_clear.sh au env RERANK_MODE=off \\
        LLM_RERANK_LOG=runs/au/A2A3.llm.jsonl \\
        python eval/run_methods.py --gold runs/au/topics.json \\
        --configs e_dense,f_dense_llm --out runs/au/A2A3.json
"""
import json
import os
import re
import sys

CODE = os.getcwd()
sys.path.insert(0, os.path.join(CODE, "eval"))
sys.path.insert(0, CODE)

import config  # noqa: E402
import pipeline  # noqa: E402
import retrieval  # noqa: E402
import run_eval  # noqa: E402

LLM_POOL = 50
# The candidate list is kept under a character budget so that the prompt stays
# well inside the model's context (qwen2.5:7b-instruct under Ollama reports
# context_length 8192; the largest prompt in the reported runs was 5,624
# tokens).
CHAR_BUDGET = 13000
TITLE_CHARS = 150
ORG_CHARS = 60
DESC_CHARS_MAX = 160
DESC_CHARS_MIN = 40

LLM_RERANK_SYSTEM = (
    "You are ranking dataset records for a search request on an open data "
    "portal. You receive the request and a numbered list of candidate "
    "records (title, publisher, tags, start of the description). Choose the "
    "ten records that best satisfy the request and order them from most to "
    "least relevant. Judge relevance to the request only. Answer with JSON "
    "only, in the form {\"ranking\": [3, 17, 1, ...]}: exactly ten distinct "
    "numbers taken from the list, best first, and no other text."
)

LLM_RERANK_USER = "Request: {question}\n\nCandidates:\n{candidates}\n\nReturn the JSON now."


def _clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rstrip() + "..."


def _line(i, doc, desc_chars):
    parts = [_clip(doc.get("title"), TITLE_CHARS) or "(untitled)"]
    org = _clip(doc.get("organization"), ORG_CHARS)
    if org:
        parts.append("publisher: " + org)
    tags = [t for t in (doc.get("tags") or []) if t][:4]
    if tags:
        parts.append("tags: " + ", ".join(tags))
    desc = _clip(doc.get("description"), desc_chars)
    if desc:
        parts.append(desc)
    return "[%d] %s" % (i, " | ".join(parts))


def candidate_block(docs):
    """Numbered candidate lines, the description shortened until the block
    fits CHAR_BUDGET (deterministic: depends only on the documents)."""
    desc = DESC_CHARS_MAX
    while True:
        block = "\n".join(_line(i, d, desc) for i, d in enumerate(docs, start=1))
        if len(block) <= CHAR_BUDGET or desc <= DESC_CHARS_MIN:
            return block, desc
        desc -= 20


def parse_ranking(raw, n):
    """Valid 1-based numbers in reply order, duplicates and out-of-range dropped."""
    nums = None
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict) and isinstance(obj.get("ranking"), list):
                nums = obj["ranking"]
        except ValueError:
            nums = None
    parse_ok = nums is not None
    if nums is None:
        # Fallback: the bracketed list, or failing that every integer.
        m = re.search(r"\[([\d,\s]+)\]", raw or "")
        nums = re.findall(r"\d+", m.group(1) if m else (raw or ""))
    out, seen = [], set()
    for x in nums:
        try:
            k = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= k <= n and k not in seen:
            seen.add(k)
            out.append(k)
    return out, parse_ok


def _log(entry):
    path = os.environ.get("LLM_RERANK_LOG", "")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_e_dense(question):
    docs = retrieval.semantic_search(question, size=config.TOP_K)
    docs = retrieval.dedup_by_title(docs)
    return [d["id"] for d in retrieval.finalise(question, docs)], None


def run_f_dense_llm(question):
    docs = retrieval.semantic_search(question, size=LLM_POOL)
    docs = retrieval.dedup_by_title(docs)[:LLM_POOL]
    block, desc_chars = candidate_block(docs)
    user = LLM_RERANK_USER.format(question=question, candidates=block)
    raw = pipeline.call_llm(LLM_RERANK_SYSTEM, user)
    picked, parse_ok = parse_ranking(raw, len(docs))
    chosen = picked[:config.TOP_K]
    filled = 0
    for k in range(1, len(docs) + 1):
        if len(chosen) >= config.TOP_K:
            break
        if k not in chosen:
            chosen.append(k)
            filled += 1
    _log({"question": question, "n_candidates": len(docs),
          "candidate_ids": [d["id"] for d in docs], "desc_chars": desc_chars,
          "prompt_chars": len(LLM_RERANK_SYSTEM) + len(user), "raw": raw,
          "parse_ok": parse_ok, "valid_numbers": picked, "filled": filled})
    return [docs[k - 1]["id"] for k in chosen], None


run_eval.CONFIGS["e_dense"] = run_e_dense
run_eval.CONFIGS["f_dense_llm"] = run_f_dense_llm

if __name__ == "__main__":
    if "f_dense_llm" in sys.argv[-1] or any("f_dense_llm" in a for a in sys.argv):
        import hashlib
        print("f_dense_llm prompt sha256 (system + user template): %s" % hashlib.sha256(
            (LLM_RERANK_SYSTEM + "\n" + LLM_RERANK_USER).encode()).hexdigest())
    run_eval.main()
