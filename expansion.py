"""Query expansion runtime (schemes A + B)."""
import json
import os
import threading

import numpy as np

import config


_vocab_cache = None
_gazetteer_cache = None
_embedder = None
_lock = threading.Lock()


def _mode():
    return getattr(config, "EXPANSION_MODE", "off").lower()


def _a_active():
    return _mode() in ("a", "ab")


def _b_active():
    return _mode() in ("b", "ab")


def _place_resolver_active():
    """Whether scheme_b_place() is allowed to resolve at all."""
    return (getattr(config, "PLACE_RESOLVER", "bboxes") == "gazetteer"
            or _b_active())


def _load_vocab():
    global _vocab_cache
    if _vocab_cache is not None:
        return _vocab_cache
    path = config.VOCAB_INDEX_FILE
    if not os.path.exists(path):
        print("[warn] expansion: %s not found, Scheme A disabled "
              "(run ingest/build_vocabulary.py)" % path)
        _vocab_cache = {"vocab": [], "kind": [], "df": [],
                        "vectors": np.zeros((0, 384), dtype=np.float32)}
        return _vocab_cache
    data = np.load(path, allow_pickle=True)
    _vocab_cache = {
        "vocab": data["vocab"].tolist(),
        "kind": data["kind"].tolist(),
        "df": data["df"].tolist(),
        "vectors": data["vectors"],
    }
    print("[info] expansion: loaded %d vocabulary terms from %s"
          % (len(_vocab_cache["vocab"]), path))
    return _vocab_cache


def _load_gazetteer():
    global _gazetteer_cache
    if _gazetteer_cache is not None:
        return _gazetteer_cache
    path = config.GAZETTEER_FILE
    if not path:
        # The corpus provides no gazetteer. Same empty tables as a missing
        # file, so every lookup below simply finds nothing.
        print("[info] expansion: corpus %r has no gazetteer, Scheme B disabled"
              % getattr(config, "CORPUS", "?"))
        _gazetteer_cache = {"suburbs": {}, "lgas": {}, "states": {},
                            "features": {}}
        return _gazetteer_cache
    if not os.path.exists(path):
        print("[warn] expansion: %s not found, Scheme B disabled" % path)
        _gazetteer_cache = {"suburbs": {}, "lgas": {}, "states": {},
                            "features": {}}
        return _gazetteer_cache
    with open(path) as f:
        _gazetteer_cache = json.load(f)
    # The "features" section exists only in a gazetteer built with --water or
    # --region; one without it keeps working unchanged.
    _gazetteer_cache.setdefault("features", {})
    print("[info] expansion: loaded gazetteer (%d suburbs, %d LGAs, "
          "%d states, %d features) from %s"
          % (len(_gazetteer_cache["suburbs"]),
             len(_gazetteer_cache["lgas"]),
             len(_gazetteer_cache["states"]),
             len(_gazetteer_cache["features"]), path))
    return _gazetteer_cache


def _get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer(config.EMBED_MODEL,
                                            device=config.DEVICE)
    return _embedder


# ---- Scheme A: vocabulary top-k -------------------------------------------
_EXPANSION_TOKEN_RE = config.LANGUAGE.EXPANSION_TOKEN_RE


def scheme_a_terms(question, top_k=None, min_sim=None):
    """Return up to top_k vocabulary terms most similar to the question."""
    if not _a_active():
        return []

    top_k = top_k or config.EXPANSION_TOP_K
    min_sim = min_sim if min_sim is not None else config.EXPANSION_MIN_SIM

    vocab = _load_vocab()
    if not vocab["vocab"]:
        return []

    vec = _get_embedder().encode(question, normalize_embeddings=True)
    sims = vocab["vectors"] @ np.asarray(vec, dtype=np.float32)
    order = np.argsort(-sims)

    q_lower = question.lower()
    q_tokens = set(_EXPANSION_TOKEN_RE.findall(q_lower))
    picked = []
    seen = set()
    for idx in order:
        if sims[idx] < min_sim:
            break
        term = vocab["vocab"][idx]
        low = term.lower()
        if low in q_lower:
            continue
        tokens = set(_EXPANSION_TOKEN_RE.findall(low))
        if tokens and tokens <= q_tokens:
            continue
        if low in seen:
            continue
        seen.add(low)
        picked.append({"term": term, "kind": vocab["kind"][idx],
                       "sim": float(sims[idx])})
        if len(picked) >= top_k:
            break
    return picked


# ---- Scheme B: gazetteer lookup -------------------------------------------
_PLACES = config.PLACES
_PLACE_PREFIXES = _PLACES.PLACE_PREFIXES if _PLACES is not None else ()
_PLACE_SUFFIXES = _PLACES.PLACE_SUFFIXES if _PLACES is not None else ()


def _norm_place(name):
    if not name:
        return ""
    text = name.lower().strip().strip(".")
    text = text.split(",")[0].strip()
    if text.startswith("the "):
        text = text[4:].strip()
    for prefix in _PLACE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    for suffix in _PLACE_SUFFIXES:
        if text.endswith(suffix):
            text = text[:-len(suffix)].strip()
    return text


# Generic tail words that turn a named feature into a descriptive phrase
# ("Lachlan River catchment", "Otway Basin system").
_FEATURE_TAILS = _PLACES.FEATURE_TAILS if _PLACES is not None else ()

# PLACE_NORMALISE. Trailing words that name the KIND of local government area
# rather than the area itself.
_ADMIN_TAILS = _PLACES.ADMIN_TAILS if _PLACES is not None else ()
_NON_LATIN_RE = config.LANGUAGE.FOREIGN_SCRIPT_RE


def _a4_active():
    return getattr(config, "PLACE_NORMALISE", "off") == "a4"


def _place_keys(place):
    """Lookup keys to try, in order."""
    key = _norm_place(place)
    if not key:
        return []
    keys = [key]

    parts = key.split()
    while len(parts) > 1 and parts[-1] in _FEATURE_TAILS:
        parts = parts[:-1]
        keys.append(" ".join(parts))

    if _a4_active():
        for candidate in list(keys):
            # Non-Latin script first: the router occasionally returns the place
            # in the language of the question (a Chinese city name before
            # "Maribyrnong"), and what is left after dropping it is the name
            # the catalogue uses.
            stripped = _NON_LATIN_RE.sub(" ", candidate)
            stripped = " ".join(stripped.split())
            if stripped and stripped != candidate:
                keys.append(stripped)

        for candidate in list(keys):
            words = candidate.split()
            while len(words) > 1 and words[-1] in _ADMIN_TAILS:
                words = words[:-1]
                keys.append(" ".join(words))

    seen, ordered = set(), []
    for candidate in keys:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def scheme_b_place(place):
    """Resolve a place name to (kind, canonical_name, bbox, lga)."""
    if not _place_resolver_active():
        return None

    keys = _place_keys(place)
    if not keys:
        return None
    gaz = _load_gazetteer()

    for key in keys:
        if key in gaz["states"]:
            entry = gaz["states"][key]
            return {"kind": "state", "name": entry["name"],
                    "bbox": entry["bbox"], "lga": None}
        if key in gaz["lgas"]:
            entry = gaz["lgas"][key]
            return {"kind": "lga", "name": entry["name"],
                    "bbox": entry["bbox"], "lga": entry["name"]}
        if key in gaz["suburbs"]:
            entry = gaz["suburbs"][key]
            return {"kind": "suburb", "name": key, "bbox": entry["bbox"],
                    "lga": entry["lga_display"]}
        # Hydrographic and region features come last: an administrative
        # match is always the more specific answer when both exist.
        if key in gaz.get("features", {}):
            entry = gaz["features"][key]
            return {"kind": "feature/%s" % entry.get("code", "?"),
                    "name": entry["name"], "bbox": entry["bbox"],
                    "lga": None}
    return None


# ---- Combined interface used by pipeline.retrieve --------------------------
def expand(question, place="", plan=None):
    """Run the active schemes and package the result for the retrievers."""
    if _mode() == "off":
        return {"terms": [], "place_hit": None, "trace": {}}

    a_hits = scheme_a_terms(question) if _a_active() else []
    # scheme_b_place() is also reachable from spatial_search() under
    # PLACE_RESOLVER=gazetteer, the place-resolution half of Scheme B.
    b_hit = scheme_b_place(place) if (_b_active() and place) else None

    terms = [h["term"] for h in a_hits]

    if b_hit and b_hit["lga"]:
        # Under mode 'b' (Scheme B only), the router's plan gates whether the
        # LGA name is injected as a keyword: no injection unless the router
        # already thinks spatial retrieval is warranted.
        gate_ok = True
        if _mode() == "b" and plan is not None:
            gate_ok = "spatial" in (plan.get("methods") or [])
        if gate_ok and b_hit["lga"].lower() not in question.lower():
            terms.append(b_hit["lga"])

    return {"terms": terms, "place_hit": b_hit,
            "trace": {"a": a_hits, "b": b_hit}}
