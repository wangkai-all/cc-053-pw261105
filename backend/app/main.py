"""FastAPI application: ledger API + static frontend."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .db import connect, init_db

app = FastAPI(title="会员积分台账", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


class ImportBody(BaseModel):
    events: list[dict]


@app.post("/api/events/import")
def import_events(body: ImportBody):
    with connect() as db:
        results = store.import_events(db, body.events)
    summary: dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {"results": results, "summary": summary}


@app.get("/api/members")
def members():
    with connect() as db:
        return store.list_members(db)


@app.get("/api/members/{member_id}/ledger")
def ledger(member_id: str):
    with connect() as db:
        data = store.get_ledger(db, member_id)
    if not data:
        raise HTTPException(404, "会员不存在或暂无已发布台账")
    return data


@app.get("/api/members/{member_id}/draft")
def draft(member_id: str):
    with connect() as db:
        data = store.get_draft(db, member_id)
    if not data:
        raise HTTPException(404, "当前没有待发布的重算草稿")
    return data


@app.post("/api/members/{member_id}/draft/publish")
def publish(member_id: str):
    with connect() as db:
        result = store.publish_draft(db, member_id)
    if not result:
        raise HTTPException(404, "当前没有待发布的重算草稿")
    return result


@app.post("/api/members/{member_id}/draft/discard")
def discard(member_id: str):
    with connect() as db:
        ok = store.discard_draft(db, member_id)
    if not ok:
        raise HTTPException(404, "当前没有待发布的重算草稿")
    return {"member_id": member_id, "discarded": True}


@app.get("/api/members/{member_id}/versions")
def versions(member_id: str):
    with connect() as db:
        return store.list_versions(db, member_id)


@app.get("/api/members/{member_id}/versions/{version}")
def version_detail(member_id: str, version: int):
    with connect() as db:
        data = store.get_version(db, member_id, version)
    if not data:
        raise HTTPException(404, "版本不存在")
    return data


@app.post("/api/demo/reset")
def demo_reset():
    with connect() as db:
        store.reset(db)
    return {"reset": True}


# ------------------------------------------------------------- static frontend
DIST = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                     "frontend", "dist"))
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")),
              name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.normpath(os.path.join(DIST, full_path))
        if full_path and candidate.startswith(DIST) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(DIST, "index.html"))
