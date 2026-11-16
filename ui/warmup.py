"""Warm the system up before the first real question.

    python ui/warmup.py [--question "population data"] [--keep-alive 2h]
"""
import argparse
import json
import os
import sys
import time

import httpx

GATEWAY = "http://127.0.0.1:%s" % os.environ.get("UI_PORT", "8000")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = "qwen2.5:7b-instruct"
CORPORA = ("au", "uk", "us")


def load_model(keep_alive):
    t0 = time.time()
    r = httpx.post(OLLAMA + "/api/generate", timeout=600.0,
                   json={"model": MODEL, "prompt": "Say OK.", "stream": False,
                         "keep_alive": keep_alive, "options": {"num_predict": 2}})
    r.raise_for_status()
    secs = time.time() - t0
    expires = ""
    try:
        ps = httpx.get(OLLAMA + "/api/ps", timeout=10.0).json()
        expires = next((m.get("expires_at", "") for m in ps.get("models", [])
                        if m.get("name") == MODEL), "")
    except Exception:
        pass
    print("  model %s loaded in %.1fs, kept until %s" % (MODEL, secs, expires or "?"))


def ask(corpus, question):
    """One run through the gateway; returns (seconds, ok, number of results)."""
    body = {"question": question, "history": [], "ask": False}
    t0, ok, n = time.time(), False, 0
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        with client.stream("POST", "%s/api/%s/run" % (GATEWAY, corpus), json=body) as res:
            res.raise_for_status()
            buf = ""
            for chunk in res.iter_text():
                buf += chunk.replace("\r\n", "\n")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.split("\n"):
                        if not line.startswith("data:"):
                            continue
                        ev = json.loads(line[5:].strip())
                        if ev["type"] == "clarify_question":
                            # Not expected with ask off; answer as Skip would.
                            client.post("%s/api/%s/reply/%s" % (GATEWAY, corpus, ev["job_id"]),
                                        json={"skip": True})
                        elif ev["type"] == "results":
                            n = len(ev.get("items", []))
                        elif ev["type"] == "done":
                            ok = bool(ev.get("ok"))
    return time.time() - t0, ok, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="population data")
    ap.add_argument("--keep-alive", default="2h")
    args = ap.parse_args()
    good = True
    t_all = time.time()

    print("warm-up a: load the language model")
    try:
        load_model(args.keep_alive)
    except Exception as e:
        good = False
        print("  failed: %s" % e)

    print('warm-up b: one question per corpus ("%s")' % args.question)
    for corpus in CORPORA:
        try:
            secs, ok, n = ask(corpus, args.question)
            print("  %s: %.1fs, %d results%s" % (corpus, secs, n, "" if ok else ", NOT ok"))
            good = good and ok
        except Exception as e:
            good = False
            print("  %s: failed: %s" % (corpus, e))

    print("warm-up took %.1fs" % (time.time() - t_all))
    if good:
        print("ready for demo")
        return 0
    print("warm-up had errors: see above and ui/logs/")
    return 1


if __name__ == "__main__":
    sys.exit(main())
