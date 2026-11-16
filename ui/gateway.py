"""The one process the browser talks to.

    python -m ui.gateway            # 127.0.0.1:$UI_PORT (default 8000)
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from ui import settings

app = FastAPI(title="Agentic Dataset Search")


def _corpus(corpus):
    if corpus not in settings.CORPORA:
        raise HTTPException(404, "Unknown catalogue")
    return corpus


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _status_file(corpus):
    try:
        with open(os.path.join(settings.RUN_DIR, "%s.status.json" % corpus),
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


async def _worker_health(client, corpus):
    meta = settings.CORPORA[corpus]
    base = {"corpus": corpus, "label": meta["label"],
            "port": settings.worker_port(corpus)}
    try:
        res = await client.get(settings.worker_url(corpus) + "/health",
                               timeout=6.0)
        res.raise_for_status()
        return {**base, **res.json()}
    except Exception:                                          # noqa: BLE001
        status = _status_file(corpus)
        if status and status.get("state") == "error" and not _pid_alive(
                status.get("pid")):
            return {**base, "state": "error",
                    "reason": status.get("reason") or "The worker stopped."}
        if status and _pid_alive(status.get("pid")):
            return {**base, "state": "starting"}
        return {**base, "state": "error",
                "reason": "The worker for this catalogue is not running."}


@app.get("/api/health")
async def health():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_worker_health(client, c) for c in settings.CORPORA])
    return {"corpora": {r["corpus"]: r for r in results}}


@app.get("/api/{corpus}/health")
async def corpus_health(corpus: str):
    async with httpx.AsyncClient() as client:
        return await _worker_health(client, _corpus(corpus))


async def _forward_json(corpus, method, path, body=None):
    _corpus(corpus)
    async with httpx.AsyncClient() as client:
        try:
            res = await client.request(method, settings.worker_url(corpus) + path,
                                       json=body, timeout=30.0)
        except httpx.HTTPError:
            raise HTTPException(503, "This catalogue is not available right now")
    try:
        payload = res.json()
    except ValueError:
        payload = {"detail": res.text[:500]}
    return JSONResponse(payload, status_code=res.status_code)


@app.get("/api/{corpus}/examples")
async def examples(corpus: str):
    return await _forward_json(corpus, "GET", "/examples")


@app.get("/api/{corpus}/record/{record_id}")
async def record(corpus: str, record_id: str):
    return await _forward_json(corpus, "GET", "/record/" + record_id)


@app.get("/api/{corpus}/neighbours/{record_id}")
async def neighbours(corpus: str, record_id: str):
    return await _forward_json(corpus, "GET", "/neighbours/" + record_id)


@app.post("/api/{corpus}/reply/{job_id}")
async def reply(corpus: str, job_id: str, request: Request):
    return await _forward_json(corpus, "POST", "/reply/" + job_id,
                               await request.json())


@app.post("/api/{corpus}/cancel/{job_id}")
async def cancel(corpus: str, job_id: str):
    return await _forward_json(corpus, "POST", "/cancel/" + job_id)


@app.post("/api/{corpus}/run")
async def run(corpus: str, request: Request):
    _corpus(corpus)
    body = await request.json()
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
    upstream_request = client.build_request(
        "POST", settings.worker_url(corpus) + "/run", json=body)
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, "This catalogue is not available right now")
    if upstream.status_code != 200:
        detail = (await upstream.aread()).decode("utf-8", "replace")[:500]
        await upstream.aclose()
        await client.aclose()
        return JSONResponse({"detail": detail}, status_code=upstream.status_code)

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(relay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---- static frontend ------------------------------------------------------
@app.get("/api/corpora")
async def corpora():
    return {"corpora": [{"id": c, "label": m["label"]}
                        for c, m in settings.CORPORA.items()]}


@app.get("/")
async def index():
    return FileResponse(os.path.join(settings.STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache"})


if os.path.isdir(settings.STATIC_DIR):
    app.mount("/static", StaticFiles(directory=settings.STATIC_DIR),
              name="static")


def main():
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.GATEWAY_PORT,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
