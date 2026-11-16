"""Side-channel observers: record what the system does, change nothing."""
import copy
import functools
import io
import sys
import threading
import time
from contextlib import contextmanager

_local = threading.local()
_ORIGINALS = []          # (module, name, original), in install order
_INSTALLED = [False]
RECORDING_ERRORS = {"count": 0, "last": ""}

# Fields of a document dict the UI may read. Everything is copied.
_DOC_SCALARS = ("id", "title", "organization", "type", "url", "rrf_score",
                "rerank_score", "vote_score", "graph_note", "graph_relation",
                "graph_weight", "hydration")
_DOC_CONTAINERS = ("from_methods", "path_ranks", "round_ranks", "tags")


def slim_doc(doc, with_text=False):
    """A plain copy of the fields the UI reads from one document dict."""
    out = {}
    for key in _DOC_SCALARS:
        if key in doc:
            out[key] = doc[key]
    for key in _DOC_CONTAINERS:
        if key in doc:
            out[key] = copy.deepcopy(doc[key])
    if with_text:
        out["description"] = doc.get("description", "")
    return out


def slim_docs(docs, with_text=False):
    return [slim_doc(d, with_text) for d in docs]


def path_row(doc):
    """What a single retrieval path returned, for the per-path list."""
    row = {"id": doc.get("id"), "title": doc.get("title", ""),
           "organization": doc.get("organization", ""),
           "type": doc.get("type", "")}
    note = doc.get("graph_note")
    if not note:
        desc = doc.get("description") or ""
        if desc.startswith("graph evidence:"):
            note = desc
    if note:
        row["note"] = note[:200]
    return row


# ---- sink plumbing --------------------------------------------------------
def current_sink():
    return getattr(_local, "sink", None)


@contextmanager
def activate(sink):
    """Make `sink` receive the records made by this thread."""
    previous = getattr(_local, "sink", None)
    _local.sink = sink
    try:
        yield sink
    finally:
        _local.sink = previous


def _emit(sink, kind, phase, **payload):
    try:
        sink.record(kind, phase, payload)
    except Exception as exc:                                   # noqa: BLE001
        RECORDING_ERRORS["count"] += 1
        RECORDING_ERRORS["last"] = "%s.%s: %r" % (kind, phase, exc)


def _safe(fn, *args):
    """Run a snapshot helper; a failure there must not reach the pipeline."""
    try:
        return fn(*args)
    except Exception as exc:                                   # noqa: BLE001
        RECORDING_ERRORS["count"] += 1
        RECORDING_ERRORS["last"] = "%s: %r" % (getattr(fn, "__name__", "?"), exc)
        return None


def _wrapper(original, kind, start=None, end=None):
    """Build the transparent wrapper."""

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        sink = current_sink()
        if sink is None:
            return original(*args, **kwargs)
        payload = _safe(start, args, kwargs) if start else {}
        _emit(sink, kind, "start", **(payload or {}))
        began = time.monotonic()
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            _emit(sink, kind, "error", error=repr(exc),
                  seconds=time.monotonic() - began)
            raise
        seconds = time.monotonic() - began
        payload = _safe(end, args, kwargs, result, seconds) if end else {}
        _emit(sink, kind, "end", seconds=seconds, **(payload or {}))
        return result

    wrapper.__observed__ = True
    return wrapper


def _arg(args, kwargs, index, name, default=None):
    if len(args) > index:
        return args[index]
    return kwargs.get(name, default)


# ---- what each observer records ------------------------------------------
def _memory_start(args, kwargs):
    history = _arg(args, kwargs, 1, "history") or []
    return {"question": _arg(args, kwargs, 0, "question"),
            "history_len": len(history)}


def _memory_end(args, kwargs, result, seconds):
    history = _arg(args, kwargs, 1, "history") or []
    return {"question": _arg(args, kwargs, 0, "question"), "result": result,
            "history_len": len(history)}


def _route_start(args, kwargs):
    return {"question": _arg(args, kwargs, 0, "question")}


def _route_end(args, kwargs, result, seconds):
    return {"question": _arg(args, kwargs, 0, "question"),
            "plan": copy.deepcopy(result)}


def _retrieve_start(args, kwargs):
    return {"question": _arg(args, kwargs, 0, "question"),
            "plan": copy.deepcopy(_arg(args, kwargs, 1, "plan"))}


def _retrieve_end(args, kwargs, result, seconds):
    return {"sizes": {k: len(v) for k, v in result.items()}}


def make_rewrite_wrapper(original, trace_list):
    """rewrite.rewrite needs the trace list to see whether this call added an
    entry (it adds none when the rewrite mode is off)."""

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        sink = current_sink()
        if sink is None:
            return original(*args, **kwargs)
        before = len(trace_list)
        question = _arg(args, kwargs, 0, "question")
        _emit(sink, "rewrite", "start", question=question)
        began = time.monotonic()
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            _emit(sink, "rewrite", "error", error=repr(exc),
                  seconds=time.monotonic() - began)
            raise
        seconds = time.monotonic() - began

        def snapshot():
            entry = None
            if len(trace_list) > before:
                entry = copy.deepcopy(trace_list[-1])
            return {"question": question, "result": result, "entry": entry}
        payload = _safe(snapshot) or {"question": question, "result": result,
                                      "entry": None}
        _emit(sink, "rewrite", "end", seconds=seconds, **payload)
        return result

    wrapper.__observed__ = True
    return wrapper


def _path_start_factory(path):
    def start(args, kwargs):
        if path == "keyword":
            return {"path": path, "query": _arg(args, kwargs, 0, "query"),
                    "phrase": _arg(args, kwargs, 2, "phrase", "") or "",
                    "size": _arg(args, kwargs, 1, "size")}
        if path == "publisher":
            return {"path": path, "query": _arg(args, kwargs, 1, "query"),
                    "phrase": _arg(args, kwargs, 3, "phrase", "") or "",
                    "size": _arg(args, kwargs, 2, "size")}
        if path == "semantic":
            return {"path": path, "query": _arg(args, kwargs, 0, "query"),
                    "size": _arg(args, kwargs, 1, "size"),
                    "custom_vector": _arg(args, kwargs, 2, "vector") is not None}
        if path == "spatial":
            return {"path": path, "place": _arg(args, kwargs, 0, "place"),
                    "query": _arg(args, kwargs, 1, "query", ""),
                    "phrase": _arg(args, kwargs, 3, "phrase", "") or "",
                    "size": _arg(args, kwargs, 2, "size")}
        return {"path": path, "intent": _arg(args, kwargs, 0, "intent"),
                "entity": _arg(args, kwargs, 1, "entity", ""),
                "query": _arg(args, kwargs, 3, "query", ""),
                "size": _arg(args, kwargs, 2, "size")}
    return start


def _path_end_factory(path):
    def end(args, kwargs, result, seconds):
        return {"path": path, "rows": [path_row(d) for d in result]}
    return end


def _fuse_start(args, kwargs):
    rank_lists = _arg(args, kwargs, 0, "rank_lists") or {}
    return {"inputs": {k: len(v) for k, v in rank_lists.items()}}


def _fuse_end(args, kwargs, result, seconds):
    rank_lists = _arg(args, kwargs, 0, "rank_lists") or {}
    out = {"inputs": {k: len(v) for k, v in rank_lists.items()},
           "docs": slim_docs(result)}
    # The comparison view (ui/compare.py) re-ranks a first-round pool after the
    # job, so it needs every field the re-ranker reads (description, tags, the
    # graph fields), copied now: later rounds and the re-ranker write into
    # these dicts.
    if getattr(current_sink(), "capture_pools", False):
        out["pool"] = copy.deepcopy(list(result))
    return out


def _decide_start(args, kwargs):
    docs = _arg(args, kwargs, 1, "docs") or []
    return {"question": _arg(args, kwargs, 0, "question"),
            "evidence": len(docs),
            "ask_allowed": bool(_arg(args, kwargs, 2, "ask_allowed", False))}


def _decide_end(args, kwargs, result, seconds):
    return {"decision": copy.deepcopy(result)}


def _clarify_start(args, kwargs):
    docs = _arg(args, kwargs, 1, "docs") or []
    return {"question": _arg(args, kwargs, 0, "question"),
            "evidence": len(docs)}


def _clarify_end(args, kwargs, result, seconds):
    verdict, raw = result
    return {"verdict": copy.deepcopy(verdict), "raw": raw}


def _slot_start(args, kwargs):
    # slot_clarify(question, plan, docs): same record as clarify, which the
    # engine turns into the same clarify_check event.
    docs = _arg(args, kwargs, 2, "docs") or []
    return {"question": _arg(args, kwargs, 0, "question"),
            "evidence": len(docs)}


def _rerank_start(args, kwargs):
    docs = _arg(args, kwargs, 1, "docs") or []
    # Order and fused scores before the call: rerank.rerank() writes
    # rerank_score and vote_score into these same dicts.
    return {"candidates": len(docs),
            "before": [{"id": d.get("id"), "rrf_score": d.get("rrf_score")}
                       for d in docs]}


def _rerank_end(args, kwargs, result, seconds):
    import rerank as _rerank
    return {"candidates": len(result), "after": slim_docs(result),
            "trace": copy.deepcopy(dict(_rerank.TRACE))}


def _finalise_start(args, kwargs):
    docs = _arg(args, kwargs, 1, "docs") or []
    return {"candidates": len(docs)}


def _finalise_end(args, kwargs, result, seconds):
    return {"top": slim_docs(result, with_text=True)}


def _generate_start(args, kwargs):
    return {"question": _arg(args, kwargs, 0, "question")}


def _generate_end(args, kwargs, result, seconds):
    return {"text": result,
            "feedback": kwargs.get("feedback", "") or "",
            "hint_used": bool(kwargs.get("hint", "")),
            "temperature": kwargs.get("temperature", 0.2)}


def _validate_start(args, kwargs):
    return {"question": _arg(args, kwargs, 0, "question")}


def _validate_end(args, kwargs, result, seconds):
    grounded, relevant, raw_g, raw_r = result
    return {"answer": _arg(args, kwargs, 1, "answer"),
            "grounded": bool(grounded), "relevant": bool(relevant),
            "citation_detail": raw_g, "relevance_detail": raw_r}


# ---- install / uninstall --------------------------------------------------
def _patch(module, name, wrapper):
    original = getattr(module, name)
    if getattr(original, "__observed__", False):
        return
    _ORIGINALS.append((module, name, original))
    setattr(module, name, wrapper(original))


def install():
    """Install every observer. Idempotent. Must run after the root modules
    are imported and before the first job."""
    if _INSTALLED[0]:
        return
    import pipeline
    import retrieval
    import rerank
    import rewrite

    _patch(pipeline, "memory_rewrite",
           lambda f: _wrapper(f, "memory", _memory_start, _memory_end))
    _patch(pipeline, "route",
           lambda f: _wrapper(f, "route", _route_start, _route_end))
    _patch(pipeline, "retrieve",
           lambda f: _wrapper(f, "retrieve", _retrieve_start, _retrieve_end))
    _patch(rewrite, "rewrite",
           lambda f: make_rewrite_wrapper(f, rewrite.TRACE))
    for path, fname in (("keyword", "keyword_search"),
                        ("semantic", "semantic_search"),
                        ("spatial", "spatial_search"),
                        ("graph", "graph_search"),
                        # Called only under ENTITY_RESOLVE.
                        ("publisher", "publisher_search")):
        _patch(retrieval, fname,
               lambda f, p=path: _wrapper(f, "path", _path_start_factory(p),
                                          _path_end_factory(p)))
    _patch(retrieval, "rrf_fuse",
           lambda f: _wrapper(f, "fuse", _fuse_start, _fuse_end))
    _patch(pipeline, "decide",
           lambda f: _wrapper(f, "decide", _decide_start, _decide_end))
    _patch(pipeline, "clarify",
           lambda f: _wrapper(f, "clarify", _clarify_start, _clarify_end))
    _patch(pipeline, "slot_clarify",
           lambda f: _wrapper(f, "clarify", _slot_start, _clarify_end))
    # Same signature and record as slot_clarify.
    _patch(pipeline, "grounded_clarify",
           lambda f: _wrapper(f, "clarify", _slot_start, _clarify_end))
    _patch(rerank, "rerank",
           lambda f: _wrapper(f, "rerank", _rerank_start, _rerank_end))
    _patch(retrieval, "finalise",
           lambda f: _wrapper(f, "finalise", _finalise_start, _finalise_end))
    _patch(pipeline, "generate",
           lambda f: _wrapper(f, "generate", _generate_start, _generate_end))
    _patch(pipeline, "validate",
           lambda f: _wrapper(f, "validate", _validate_start, _validate_end))
    _INSTALLED[0] = True


def uninstall():
    """Put the originals back (tests)."""
    while _ORIGINALS:
        module, name, original = _ORIGINALS.pop()
        setattr(module, name, original)
    _INSTALLED[0] = False


def original(module, name):
    """The function install() replaced, or the attribute itself when nothing
    was replaced. Calling it records nothing, whatever sink is active."""
    for m, n, fn in _ORIGINALS:
        if m is module and n == name:
            return fn
    return getattr(module, name)


def installed_names():
    return ["%s.%s" % (m.__name__, n) for m, n, _ in _ORIGINALS]


# ---- stdout tee -----------------------------------------------------------
class _Tee(io.TextIOBase):
    """Forwards every write to the real stream unchanged, and hands complete
    lines written by a job thread to that thread's sink as notices."""

    def __init__(self, stream):
        self._stream = stream
        self._buffers = {}

    def write(self, text):
        written = self._stream.write(text)
        sink = current_sink()
        if sink is not None and text:
            ident = threading.get_ident()
            buf = self._buffers.get(ident, "") + text
            *lines, rest = buf.split("\n")
            self._buffers[ident] = rest
            for line in lines:
                if line.strip():
                    _emit(sink, "stdout", "line", line=line)
        return written

    def flush(self):
        return self._stream.flush()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_stdout_tee():
    if isinstance(sys.stdout, _Tee):
        return
    sys.stdout = _Tee(sys.stdout)


def uninstall_stdout_tee():
    if isinstance(sys.stdout, _Tee):
        sys.stdout = sys.stdout._stream
