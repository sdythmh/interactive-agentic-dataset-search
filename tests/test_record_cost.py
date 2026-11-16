"""Offline checks for EVAL_RECORD_COST (eval/cost_record.py). No backend, no LLM:
fake modules and a fake chat client. Run: python tests/test_record_cost.py
"""
import ast
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(ROOT, "eval"))
import cost_record  # noqa: E402

fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def fake_client():
    calls = []

    class Usage:
        prompt_tokens, completion_tokens = 11, 3

    def create(**kw):
        calls.append(kw)
        return types.SimpleNamespace(usage=Usage(), choices=["x"], kw=kw)

    c = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    return c, calls


def fake_modules():
    client, calls = fake_client()
    pipeline = types.SimpleNamespace(llm=client)
    shared = {"doc": {"id": "a"}}
    plan = {"methods": ["keyword"]}

    def route(q):
        pipeline.llm.chat.completions.create(model="m", q=q)
        return plan

    def keyword_search(q, size=10):
        return [shared["doc"]]

    def answer_stage(q):
        yield "generation", "x"
        pipeline.llm.chat.completions.create(model="m")
        yield "answer", "y"
        return "ret"

    pipeline.route = route
    pipeline.answer_stage = answer_stage
    retrieval = types.SimpleNamespace(keyword_search=keyword_search)
    return {"pipeline": pipeline, "retrieval": retrieval}, calls, plan, shared


# 1. off: nothing wrapped, run_eval guards every use
mods, calls, plan, shared = fake_modules()
orig = (mods["pipeline"].route, mods["retrieval"].keyword_search, mods["pipeline"].llm.chat.completions.create)
check(not cost_record.enabled("") and not cost_record.enabled("0") and not cost_record.enabled(None), "enabled() is False for unset, empty, 0")
check(all(cost_record.enabled(v) for v in ("1", "on", "TRUE", "yes")), "enabled() is True for 1, on, true, yes")
check((mods["pipeline"].route, mods["retrieval"].keyword_search, mods["pipeline"].llm.chat.completions.create) == orig,
      "off: no function or client method is replaced (install not called)")
src = open(os.path.join(ROOT, "eval", "run_eval.py")).read()
lines = src.splitlines()
guards = [i for i, l in enumerate(lines) if l.strip() == "if cost_rec is not None:"]
check(len(guards) == 2 and all("cost_rec." in lines[i + 1] for i in guards),
      "run_eval.py: both per-row cost calls sit directly under `if cost_rec is not None:`")
cost_key = [i for i, l in enumerate(lines) if 'records[-1]["cost"]' in l]
check(len(cost_key) == 1 and cost_key[0] - 1 in guards, "run_eval.py: records[-1][\"cost\"] is written only under the guard")
imp = [i for i, l in enumerate(lines) if l.strip() == "import cost_record"]
check(len(imp) == 1 and "EVAL_RECORD_COST" in lines[imp[0] - 1], "run_eval.py: cost_record is imported only when EVAL_RECORD_COST is set")
check(ast.parse(src) is not None, "run_eval.py parses")

# 2. on: wrappers pass through
mods, calls, plan, shared = fake_modules()
rec = cost_record.install(mods)
r = mods["pipeline"].route("q1")
check(r is plan, "route returns the same plan object")
d = mods["retrieval"].keyword_search("q", size=5)
check(d[0] is shared["doc"], "keyword_search returns the same documents")
check(calls[0] == {"model": "m", "q": "q1"}, "client receives the arguments unchanged")
rec.begin_row()
mods["pipeline"].route("q2")
items = []
g = mods["pipeline"].answer_stage("q")
try:
    while True:
        items.append(next(g))
except StopIteration as stop:
    ret = stop.value
check(items == [("generation", "x"), ("answer", "y")] and ret == "ret", "generator yields the same items and returns the same value")
mods["retrieval"].keyword_search("q")
c = rec.end_row()
check(c["llm_calls"] == 2 and c["prompt_tokens"] == 22 and c["completion_tokens"] == 6, "calls and usage tokens summed (2 calls, 22 + 6)")
check(c["llm_by_stage"]["pipeline.route"]["calls"] == 1 and c["llm_by_stage"]["pipeline.answer_stage"]["calls"] == 1,
      "each LLM call is attributed to the innermost stage")
check(c["stages"]["pipeline.route"]["calls"] == 1 and c["stages"]["retrieval.keyword_search"]["calls"] == 1
      and c["stages"]["pipeline.answer_stage"]["calls"] == 1, "stage counts")
check(all(v["seconds"] >= 0 for v in c["stages"].values()), "stage seconds are non-negative")
rec.begin_row()
check(rec.end_row()["llm_calls"] == 0, "begin_row resets the counters")
check(set(c) == {"llm_calls", "prompt_tokens", "completion_tokens", "usage_missing", "llm_seconds", "llm_by_stage", "stages"},
      "the cost record has exactly the documented keys")

# 3. missing usage
mods, calls, plan, shared = fake_modules()
mods["pipeline"].llm.chat.completions.create = lambda **kw: types.SimpleNamespace(usage=None)
rec = cost_record.install(mods)
mods["pipeline"].llm.chat.completions.create(x=1)
check(rec.end_row()["usage_missing"] == 1, "a response without usage is counted as missing")

# 4. an exception passes through and the stack is restored
mods, calls, plan, shared = fake_modules()
def boom(q):
    raise ValueError("x")
mods["pipeline"].route = boom
rec = cost_record.install(mods)
try:
    mods["pipeline"].route("q")
    check(False, "exception passes through")
except ValueError:
    check(rec.stack == [], "exception passes through and the stage stack is empty again")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
