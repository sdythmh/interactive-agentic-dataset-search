"""Offline source checks for EVAL_RECORD_COST in eval/run_vague.py. With the
switch off nothing may be imported, wrapped or written: every line of it
sits under the switch test or under `if cost_rec is not None`. The wrapper
itself is checked by tests/test_record_cost.py.

Usage:  python tests/test_vague_cost.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "eval", "run_vague.py")).read()
lines = src.splitlines()
fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


check(ast.parse(src) is not None, "run_vague.py parses")
imp = [i for i, l in enumerate(lines) if l.strip() == "import cost_record"]
check(len(imp) == 1 and "EVAL_RECORD_COST" in lines[imp[0] - 1],
      "cost_record imported once, only when EVAL_RECORD_COST is set")
guards = [i for i, l in enumerate(lines) if l.strip() == "if cost_rec is not None:"]
check(len(guards) == 2 and "cost_rec.begin_row()" in lines[guards[0] + 1]
      and 'rec["cost"] = cost_rec.end_row()' in lines[guards[1] + 1],
      "begin_row and the cost key sit directly under `if cost_rec is not None:`")
uses = [l for l in lines if "cost_rec" in l and not l.strip().startswith("#")
        and l.strip() != "import cost_record" and '"eval_record_cost"' not in l]
check(all(("cost_rec = None" in l or "cost_rec = cost_record.install" in l
           or "if cost_rec is not None" in l or "cost_rec.begin_row()" in l
           or "cost_rec.end_row()" in l) for l in uses), "no other use of cost_rec")
hdr = [l for l in lines if '"eval_record_cost"' in l]
check(len(hdr) == 1 and "if cost_rec is not None else {}" in hdr[0],
      "header key only when on")
print("\n%s" % ("all passed" if not fails else "FAILED: %s" % fails))
sys.exit(1 if fails else 0)
