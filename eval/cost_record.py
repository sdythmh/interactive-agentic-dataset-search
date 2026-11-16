"""EVAL_RECORD_COST: per-row cost recording for eval/run_eval.py (evaluation side only)."""
import functools
import time

STAGES = (
    ("pipeline", "memory_rewrite"), ("pipeline", "route"), ("pipeline", "retrieve"),
    ("pipeline", "decide"), ("pipeline", "clarify"), ("pipeline", "slot_clarify"),
    ("pipeline", "generate"), ("pipeline", "validate"), ("pipeline", "answer_stage"),
    ("retrieval", "keyword_search"), ("retrieval", "semantic_search"),
    ("retrieval", "spatial_search"), ("retrieval", "graph_search"),
    ("retrieval", "rrf_fuse"), ("retrieval", "finalise"),
    ("rerank", "rerank"), ("rewrite", "rewrite"),
    # Only ever called under ENTITY_RESOLVE=link_*.
    ("retrieval", "link_publisher"),
)
CLIENTS = (("pipeline", "llm"), ("rewrite", "_client"), ("hyde", "llm"))


def enabled(value):
    return str(value or "0").lower() in ("1", "on", "true", "yes")


class Recorder:
    def __init__(self):
        self.stack = []
        self.reset()

    def reset(self):
        self.llm = {"calls": 0, "prompt": 0, "completion": 0, "missing": 0, "seconds": 0.0}
        self.by_stage = {}
        self.stages = {}

    # -- wrappers ---------------------------------------------------------
    def stage(self, name, fn):
        rec = self

        if _is_generator_function(fn):
            @functools.wraps(fn)
            def gen(*a, **k):
                inner = fn(*a, **k)
                rec.stack.append(name)
                try:
                    while True:
                        t = time.perf_counter()
                        try:
                            item = next(inner)
                        except StopIteration as stop:
                            rec._add(name, time.perf_counter() - t, count=True)
                            return stop.value
                        rec._add(name, time.perf_counter() - t, count=False)
                        rec.stack.pop()
                        try:
                            yield item
                        finally:
                            rec.stack.append(name)
                finally:
                    if rec.stack and rec.stack[-1] == name:
                        rec.stack.pop()
            return gen

        @functools.wraps(fn)
        def wrapper(*a, **k):
            rec.stack.append(name)
            t = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                rec._add(name, time.perf_counter() - t, count=True)
                rec.stack.pop()
        return wrapper

    def create(self, fn):
        rec = self

        @functools.wraps(fn)
        def wrapper(*a, **k):
            t = time.perf_counter()
            resp = fn(*a, **k)
            dt = time.perf_counter() - t
            where = rec.stack[-1] if rec.stack else "other"
            usage = getattr(resp, "usage", None)
            slot = rec.by_stage.setdefault(where, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
            slot["calls"] += 1
            rec.llm["calls"] += 1
            rec.llm["seconds"] += dt
            if usage is None:
                rec.llm["missing"] += 1
            else:
                p = int(getattr(usage, "prompt_tokens", 0) or 0)
                c = int(getattr(usage, "completion_tokens", 0) or 0)
                rec.llm["prompt"] += p; rec.llm["completion"] += c
                slot["prompt_tokens"] += p; slot["completion_tokens"] += c
            return resp
        return wrapper

    def _add(self, name, dt, count):
        s = self.stages.setdefault(name, {"calls": 0, "seconds": 0.0})
        s["seconds"] += dt
        if count:
            s["calls"] += 1

    # -- per row ----------------------------------------------------------
    def begin_row(self):
        self.stack.clear()
        self.reset()

    def end_row(self):
        return {
            "llm_calls": self.llm["calls"],
            "prompt_tokens": self.llm["prompt"],
            "completion_tokens": self.llm["completion"],
            "usage_missing": self.llm["missing"],
            "llm_seconds": round(self.llm["seconds"], 3),
            "llm_by_stage": {k: dict(v) for k, v in sorted(self.by_stage.items())},
            "stages": {k: {"calls": v["calls"], "seconds": round(v["seconds"], 3)}
                       for k, v in sorted(self.stages.items())},
        }


def _is_generator_function(fn):
    import inspect
    return inspect.isgeneratorfunction(fn)


def install(modules):
    """modules: {"pipeline": module, "retrieval": ..., "rerank": ..., "rewrite": ..., "hyde": ...}.
    Wraps what exists; returns the Recorder."""
    rec = Recorder()
    for mod, attr in STAGES:
        m = modules.get(mod)
        fn = getattr(m, attr, None) if m is not None else None
        if callable(fn):
            setattr(m, attr, rec.stage("%s.%s" % (mod, attr), fn))
    for mod, attr in CLIENTS:
        m = modules.get(mod)
        client = getattr(m, attr, None) if m is not None else None
        comp = getattr(getattr(client, "chat", None), "completions", None)
        if comp is not None and callable(getattr(comp, "create", None)):
            comp.create = rec.create(comp.create)
    return rec
