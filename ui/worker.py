"""One worker process per corpus.

    python -m ui.worker --corpus au [--port 8101]
"""
# Environment first: nothing may import torch before CUDA is hidden.
import os
import sys

os.environ["CUDA_VISIBLE_DEVICES"] = ""
for _k, _v in (("HF_HUB_DISABLE_PROGRESS_BARS", "1"),
               ("HF_HUB_VERBOSITY", "error"),
               ("TRANSFORMERS_VERBOSITY", "error"),
               ("TOKENIZERS_PARALLELISM", "false"),
               ("HF_HUB_OFFLINE", "1"),
               ("TRANSFORMERS_OFFLINE", "1")):
    os.environ.setdefault(_k, _v)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import argparse
import asyncio
import json
import threading
import time
import urllib.request
import uuid

from ui import settings


# ---- status file ----------------------------------------------------------
def write_status(corpus, state, reason=None, **extra):
    try:
        os.makedirs(settings.RUN_DIR, exist_ok=True)
        path = os.path.join(settings.RUN_DIR, "%s.status.json" % corpus)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"state": state, "reason": reason, "pid": os.getpid(),
                       "time": time.time(), **extra}, f)
    except OSError:
        pass


def sse(event):
    return "id: %s\nevent: %s\ndata: %s\n\n" % (
        event.get("seq", 0), event.get("type", "message"),
        json.dumps(event, ensure_ascii=False))


# ---- the app --------------------------------------------------------------
class State:
    """What the endpoints share. Built once, in startup()."""

    def __init__(self, corpus):
        self.corpus = corpus
        self.config_ok = False
        self.problems = []
        self.info = {}
        self.warm = {"embedder": False, "reranker": False}
        self.startup_warnings = []
        self.engine = None
        self.records = None
        self.jobs = {}
        self._health_cache = (0.0, None)
        self._health_lock = threading.Lock()


def startup(corpus):
    """Self-check, warm-up, observers. Raises SystemExit(2) on a mismatch."""
    state = State(corpus)
    write_status(corpus, "starting")

    from ui import selfcheck
    ok, problems, info = selfcheck.check(corpus)
    state.config_ok, state.problems, state.info = ok, problems, info
    if not ok:
        reason = "configuration does not match the env file for %s: %s" % (
            corpus, "; ".join(problems))
        print("[ui.worker] " + reason, file=sys.stderr)
        write_status(corpus, "error", reason, problems=problems)
        raise SystemExit(2)

    import config
    import retrieval
    import rerank
    from ui import engine as engine_mod
    from ui import observe
    from ui.records import Records

    # The index belongs to this corpus? (a mismatch is a configuration
    # error; an unreachable index is only reported by /health.)
    try:
        retrieval.corpus_size()
    except SystemExit as exc:
        text = str(exc)
        if "built by corpus" in text or "carries no _meta" in text:
            write_status(corpus, "error", text)
            print("[ui.worker] " + text, file=sys.stderr)
            raise SystemExit(2)
        state.startup_warnings.append(text)
    except Exception as exc:                                   # noqa: BLE001
        state.startup_warnings.append(repr(exc))

    # Warm the two local models. No language-model call.
    t0 = time.time()
    try:
        retrieval.encode_query("warm up")
        state.warm["embedder"] = True
    except Exception as exc:                                   # noqa: BLE001
        state.startup_warnings.append("embedder: %r" % exc)
    try:
        if rerank.active():
            rerank.cross_scores("warm up", [
                {"id": "w1", "title": "warm up", "description": "warm up",
                 "type": "dataset", "tags": [], "organization": ""},
                {"id": "w2", "title": "warm", "description": "up",
                 "type": "dataset", "tags": [], "organization": ""}])
        state.warm["reranker"] = True
    except Exception as exc:                                   # noqa: BLE001
        state.startup_warnings.append("reranker: %r" % exc)

    observe.install()
    observe.install_stdout_tee()
    state.engine = engine_mod.Engine(corpus, ask_timeout=settings.ASK_TIMEOUT)
    state.engine.reset_traces()        # warm-up must not count as a run
    state.records = Records()
    state.info["warmup_seconds"] = round(time.time() - t0, 1)
    return state


def _http_json(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect_health(state):
    import config
    out = {"corpus": state.corpus,
           "label": settings.CORPORA[state.corpus]["label"],
           "config_ok": state.config_ok}
    errors = []
    try:
        n = state.records.index_count()
        out["index"] = {"ok": True, "name": config.INDEX_NAME, "documents": n}
    except Exception as exc:                                   # noqa: BLE001
        out["index"] = {"ok": False, "name": config.INDEX_NAME,
                        "error": repr(exc)[:300]}
        errors.append("search index")
    try:
        n = state.records.graph_ping()
        out["graph"] = {"ok": True, "elements": n}
    except Exception as exc:                                   # noqa: BLE001
        out["graph"] = {"ok": False, "error": repr(exc)[:300]}
        errors.append("graph database")
    try:
        data = _http_json(config.LLM_BASE_URL.rstrip("/") + "/models")
        ids = [m.get("id") for m in data.get("data", [])]
        present = config.LLM_MODEL in ids
        out["llm"] = {"ok": present, "model": config.LLM_MODEL}
        if not present:
            out["llm"]["error"] = "model not installed"
            errors.append("language model")
    except Exception as exc:                                   # noqa: BLE001
        out["llm"] = {"ok": False, "model": config.LLM_MODEL,
                      "error": repr(exc)[:300]}
        errors.append("language model")
    out["models"] = dict(state.warm)
    out["ask_available"] = config.ASK_MODE == "on"
    # Present only when the comparison view is available, so /health is
    # what it was when it is not.
    if settings.UI_COMPARE:
        out["compare_available"] = True
    out["null_reply"] = config.NULL_REPLY
    out["waiting"] = state.engine.waiting()
    out["busy"] = state.engine.busy
    out["errors"] = errors
    return out


def health(state):
    """Cached for a few seconds so a polling UI cannot hammer the backends."""
    now = time.time()
    with state._health_lock:
        stamp, cached = state._health_cache
        if cached is None or now - stamp > 4.0:
            cached = collect_health(state)
            state._health_cache = (now, cached)
    out = dict(cached)
    out["busy"] = state.engine.busy
    out["waiting"] = state.engine.waiting()
    if not state.config_ok or out["errors"]:
        out["state"] = "error"
    elif out["busy"]:
        out["state"] = "busy"
    else:
        out["state"] = "ready"
    return out


def build_app(state):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import BaseModel
    from typing import List, Optional

    from ui.engine import Job

    app = FastAPI(title="Agentic Dataset Search (%s)" % state.corpus)

    class Message(BaseModel):
        role: str
        content: str

    class RunRequest(BaseModel):
        question: str
        history: List[Message] = []
        ask: bool = True
        # The comparison view; honoured only when the
        # worker runs with UI_COMPARE.
        compare: bool = False

    class ReplyRequest(BaseModel):
        reply: Optional[str] = None
        skip: bool = False

    @app.get("/health")
    async def get_health():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, health, state)

    @app.get("/examples")
    async def get_examples():
        try:
            data = json.load(open(settings.EXAMPLES_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return data.get(state.corpus, {"examples": [], "clarify": None})

    @app.get("/record/{record_id}")
    async def get_record(record_id: str):
        loop = asyncio.get_running_loop()
        try:
            rec = await loop.run_in_executor(None, state.records.get, record_id)
        except Exception as exc:                               # noqa: BLE001
            raise HTTPException(502, "The search index could not be reached: %r" % exc)
        if rec is None:
            raise HTTPException(404, "No such dataset in this catalogue")
        return rec

    @app.get("/neighbours/{record_id}")
    async def get_neighbours(record_id: str):
        loop = asyncio.get_running_loop()
        try:
            res = await loop.run_in_executor(None, state.records.neighbours,
                                             record_id)
        except Exception as exc:                               # noqa: BLE001
            raise HTTPException(502, "The graph database could not be reached: %r" % exc)
        if res is None:
            raise HTTPException(404, "This dataset is not in the knowledge graph")
        return res

    @app.post("/run")
    async def post_run(req: RunRequest):
        question = (req.question or "").strip()
        if not question:
            raise HTTPException(400, "Empty question")
        if not state.config_ok:
            raise HTTPException(503, "This corpus is not available")
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        job_id = uuid.uuid4().hex[:12]

        def emit(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        job = Job(job_id, state.corpus, question,
                  [m.model_dump() for m in req.history], req.ask, emit,
                  compare=req.compare)
        ticket, ahead = state.engine.take_ticket()
        # Decided before the job thread starts: once it runs, the engine is
        # busy with this very job.
        must_wait = ahead > 0 or state.engine.busy
        state.jobs[job_id] = job

        def runner():
            try:
                state.engine.run(job, ticket)
            except BaseException as exc:                       # noqa: BLE001
                emit({"seq": 9999, "t": 0.0, "type": "error", "run": 0,
                      "kind": "internal",
                      "message": "Something went wrong while searching.",
                      "detail": repr(exc)[:2000]})
                emit({"seq": 10000, "t": 0.0, "type": "done", "run": 0,
                      "ok": False, "cancelled": False, "steps": 0,
                      "duration_s": 0.0})
            finally:
                emit({"type": "_end"})

        threading.Thread(target=runner, daemon=True,
                         name="job-%s" % job_id).start()

        async def stream():
            finished = False
            try:
                if must_wait:
                    yield sse({"seq": 0, "t": 0.0, "type": "queued", "run": 0,
                               "job_id": job_id, "position": ahead,
                               "text": "Waiting for the current search to finish"})
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.get("type") == "_end":
                        finished = True
                        break
                    yield sse(event)
            finally:
                if not finished:
                    job.cancel()
                state.jobs.pop(job_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no",
                                          "X-Job-Id": job_id})

    @app.post("/reply/{job_id}")
    async def post_reply(job_id: str, body: ReplyRequest):
        import config
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such search")
        text = config.NULL_REPLY if (body.skip or not (body.reply or "").strip()) \
            else body.reply.strip()
        if not job.submit_reply(text):
            raise HTTPException(409, "That search is not waiting for an answer")
        return {"ok": True, "reply": text}

    @app.post("/cancel/{job_id}")
    async def post_cancel(job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such search")
        job.cancel()
        return {"ok": True}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", required=True,
                        choices=sorted(settings.CORPORA))
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    port = args.port or settings.worker_port(args.corpus)

    state = startup(args.corpus)
    print("[ui.worker] %s ready on %s:%d (warm-up %.1fs)%s" % (
        args.corpus, settings.HOST, port, state.info.get("warmup_seconds", 0),
        "".join("\n  warning: %s" % w for w in state.startup_warnings)),
        flush=True)
    write_status(args.corpus, "ready", port=port, info=state.info,
                 warnings=state.startup_warnings)

    import uvicorn
    app = build_app(state)
    uvicorn.run(app, host=settings.HOST, port=port, log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
