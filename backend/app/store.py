"""Event import, idempotency, late-event drafts and replay versioning."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone

from .engine import (
    EVENT_TYPES, REASONS, EventIn, apply_now, detect_conflicts, iso,
    parse_ts, replay, sort_key,
)

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- validation

def _validate(raw: dict) -> tuple[EventIn | None, str | None]:
    if not isinstance(raw, dict):
        return None, "事件必须是对象"
    event_id = raw.get("event_id")
    member_id = raw.get("member_id")
    etype = raw.get("type")
    if not event_id or not isinstance(event_id, str):
        return None, "缺少 event_id"
    if not member_id or not isinstance(member_id, str):
        return None, "缺少 member_id"
    if etype not in EVENT_TYPES:
        return None, f"未知事件类型: {etype!r}"
    occurred_at = raw.get("occurred_at")
    try:
        occurred_at = iso(parse_ts(occurred_at))
    except Exception:
        return None, "occurred_at 不是合法时间"
    seq = raw.get("seq", 0)
    if isinstance(seq, bool) or not isinstance(seq, int):
        return None, "seq 必须是整数"
    amount = raw.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return None, "amount 必须是正整数"
    expires_at = raw.get("expires_at")
    ref_event_id = raw.get("ref_event_id")
    if etype == "claim":
        if not expires_at:
            return None, "领取事件缺少 expires_at"
        try:
            expires_at = iso(parse_ts(expires_at))
        except Exception:
            return None, "expires_at 不是合法时间"
    else:
        expires_at = None
    if etype == "refund":
        if not ref_event_id or not isinstance(ref_event_id, str):
            return None, "退款事件缺少 ref_event_id"
    else:
        ref_event_id = None
    return EventIn(
        event_id=event_id, member_id=member_id, type=etype,
        occurred_at=occurred_at, seq=seq, amount=amount,
        expires_at=expires_at, ref_event_id=ref_event_id,
    ), None


def _content_hash(e: EventIn) -> str:
    canon = json.dumps({
        "member_id": e.member_id, "type": e.type,
        "occurred_at": e.occurred_at, "seq": e.seq, "amount": e.amount,
        "expires_at": e.expires_at, "ref_event_id": e.ref_event_id,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


# ------------------------------------------------------------------- queries

def _row_to_event(r) -> EventIn:
    return EventIn(
        event_id=r["event_id"], member_id=r["member_id"], type=r["type"],
        occurred_at=r["occurred_at"], seq=r["seq"], amount=r["amount"],
        expires_at=r["expires_at"], ref_event_id=r["ref_event_id"],
    )


def _events_of(db, member_id: str, status: str | None = None) -> list[EventIn]:
    if status:
        rows = db.execute(
            "SELECT * FROM events WHERE member_id=? AND status=?",
            (member_id, status)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM events WHERE member_id=?", (member_id,)).fetchall()
    return [_row_to_event(r) for r in rows]


def _published(db, member_id: str) -> dict | None:
    row = db.execute(
        "SELECT * FROM versions WHERE member_id=? AND kind='published' "
        "ORDER BY version DESC LIMIT 1", (member_id,)).fetchone()
    return row


def _draft(db, member_id: str) -> dict | None:
    return db.execute(
        "SELECT * FROM versions WHERE member_id=? AND kind='draft' "
        "ORDER BY id DESC LIMIT 1", (member_id,)).fetchone()


def _next_version(db, member_id: str) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM versions "
        "WHERE member_id=? AND kind='published'", (member_id,)).fetchone()
    return row["v"] + 1


def _store_published(db, member_id: str, payload: dict) -> int:
    version = _next_version(db, member_id)
    db.execute(
        "INSERT INTO versions(member_id, version, kind, payload, conflicts, created_at) "
        " VALUES (?,?,?,?,?,?)",
        (member_id, version, "published", json.dumps(payload, ensure_ascii=False),
         "[]", iso(_now())))
    return version


def _store_draft(db, member_id: str, payload: dict, conflicts: list) -> int:
    db.execute("DELETE FROM versions WHERE member_id=? AND kind='draft'", (member_id,))
    version = _next_version(db, member_id)
    db.execute(
        "INSERT INTO versions(member_id, version, kind, payload, conflicts, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (member_id, version, "draft", json.dumps(payload, ensure_ascii=False),
         json.dumps(conflicts, ensure_ascii=False), iso(_now())))
    return version


def _recompute_member(db, member_id: str, applied_changed: bool) -> None:
    """Recompute the published replay (if the applied set changed) and the
    draft (if pending events exist)."""
    applied = _events_of(db, member_id, "applied")
    pending = _events_of(db, member_id, "pending")
    if applied and applied_changed:
        _store_published(db, member_id, replay(applied))
    if pending:
        draft_payload = replay(applied + pending)
        pub = _published(db, member_id)
        pub_payload = json.loads(pub["payload"]) if pub else {"events": []}
        conflicts = detect_conflicts(pub_payload, draft_payload)
        _store_draft(db, member_id, draft_payload, conflicts)
    else:
        db.execute("DELETE FROM versions WHERE member_id=? AND kind='draft'",
                   (member_id,))


# -------------------------------------------------------------------- import

def import_events(db, raw_events: list) -> list[dict]:
    """Import a batch of events. Each result is one of:
    applied   - appended to the published timeline
    pending   - late-arriving; only included in a recompute draft
    duplicate - same event_id and identical content, processed once
    conflict  - same event_id but different content
    error     - validation failure
    """
    with _lock:
        results = []
        applied_touched: set[str] = set()
        pending_touched: set[str] = set()
        for raw in raw_events:
            ev, err = _validate(raw)
            if err:
                results.append({
                    "event_id": (raw or {}).get("event_id") if isinstance(raw, dict) else None,
                    "status": "error", "detail": err,
                })
                continue
            existing = db.execute(
                "SELECT content_hash FROM events WHERE event_id=?",
                (ev.event_id,)).fetchone()
            if existing:
                if existing["content_hash"] == _content_hash(ev):
                    results.append({"event_id": ev.event_id, "status": "duplicate",
                                    "detail": "内容相同，已处理过，跳过"})
                else:
                    results.append({"event_id": ev.event_id, "status": "conflict",
                                    "detail": "事件编号已存在但内容不一致"})
                continue
            applied = _events_of(db, ev.member_id, "applied")
            has_draft = _draft(db, ev.member_id) is not None
            late = has_draft or bool(applied) and max(sort_key(a) for a in applied) > sort_key(ev)
            status = "pending" if late else "applied"
            db.execute(
                "INSERT INTO events(event_id, member_id, type, occurred_at, seq, amount, "
                "expires_at, ref_event_id, status, content_hash, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ev.event_id, ev.member_id, ev.type, ev.occurred_at, ev.seq,
                 ev.amount, ev.expires_at, ev.ref_event_id, status,
                 _content_hash(ev), iso(_now())))
            (applied_touched if status == "applied" else pending_touched).add(ev.member_id)
            results.append({
                "event_id": ev.event_id, "status": status,
                "detail": ("晚到事件，已生成重算草稿，待发布" if late
                           else "已并入正式回放"),
            })
        for member_id in applied_touched | pending_touched:
            _recompute_member(db, member_id, member_id in applied_touched)
        return results


# -------------------------------------------------------------------- drafts

def publish_draft(db, member_id: str) -> dict | None:
    with _lock:
        draft = _draft(db, member_id)
        if not draft:
            return None
        db.execute(
            "UPDATE events SET status='applied' WHERE member_id=? AND status='pending'",
            (member_id,))
        db.execute(
            "INSERT INTO versions(member_id, version, kind, payload, conflicts, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (member_id, draft["version"], "published", draft["payload"], "[]",
             iso(_now())))
        db.execute("DELETE FROM versions WHERE member_id=? AND kind='draft'",
                   (member_id,))
        return {"member_id": member_id, "version": draft["version"]}


def discard_draft(db, member_id: str) -> bool:
    with _lock:
        if not _draft(db, member_id):
            return False
        db.execute("DELETE FROM events WHERE member_id=? AND status='pending'",
                   (member_id,))
        db.execute("DELETE FROM versions WHERE member_id=? AND kind='draft'",
                   (member_id,))
        return True


# -------------------------------------------------------------------- views

def list_members(db) -> list[dict]:
    now = _now()
    out = []
    for row in db.execute(
            "SELECT member_id, MIN(created_at) AS first_seen FROM events "
            "GROUP BY member_id ORDER BY member_id").fetchall():
        mid = row["member_id"]
        pub = _published(db, mid)
        totals = {"remaining": 0, "consumed": 0, "expired": 0}
        version = 0
        if pub:
            payload = apply_now(json.loads(pub["payload"]), now)
            totals = payload["totals"]
            version = pub["version"]
        out.append({
            "member_id": mid,
            "version": version,
            "totals": totals,
            "has_draft": _draft(db, mid) is not None,
            "pending_events": db.execute(
                "SELECT COUNT(*) AS c FROM events WHERE member_id=? AND status='pending'",
                (mid,)).fetchone()["c"],
        })
    return out


def get_ledger(db, member_id: str) -> dict | None:
    pub = _published(db, member_id)
    if not pub:
        return None
    now = _now()
    payload = apply_now(json.loads(pub["payload"]), now)
    pending = db.execute(
        "SELECT event_id FROM events WHERE member_id=? AND status='pending' "
        "ORDER BY occurred_at, seq, event_id", (member_id,)).fetchall()
    return {
        "member_id": member_id,
        "version": pub["version"],
        "published_at": pub["created_at"],
        "pending_event_ids": [r["event_id"] for r in pending],
        "has_draft": _draft(db, member_id) is not None,
        "reasons": REASONS,
        **payload,
    }


def get_draft(db, member_id: str) -> dict | None:
    draft = _draft(db, member_id)
    if not draft:
        return None
    now = _now()
    payload = apply_now(json.loads(draft["payload"]), now)
    pub = _published(db, member_id)
    base = None
    if pub:
        base_payload = apply_now(json.loads(pub["payload"]), now)
        base = {"version": pub["version"], "totals": base_payload["totals"],
                "events": base_payload["events"]}
    pending = db.execute(
        "SELECT event_id FROM events WHERE member_id=? AND status='pending' "
        "ORDER BY occurred_at, seq, event_id", (member_id,)).fetchall()
    return {
        "member_id": member_id,
        "draft_version": draft["version"],
        "created_at": draft["created_at"],
        "conflicts": json.loads(draft["conflicts"]),
        "pending_event_ids": [r["event_id"] for r in pending],
        "base": base,
        "reasons": REASONS,
        **payload,
    }


def list_versions(db, member_id: str) -> list[dict]:
    rows = db.execute(
        "SELECT version, payload, created_at FROM versions "
        "WHERE member_id=? AND kind='published' ORDER BY version DESC",
        (member_id,)).fetchall()
    return [{
        "version": r["version"],
        "created_at": r["created_at"],
        "totals": json.loads(r["payload"])["totals"],
        "event_count": len(json.loads(r["payload"])["events"]),
    } for r in rows]


def get_version(db, member_id: str, version: int) -> dict | None:
    row = db.execute(
        "SELECT * FROM versions WHERE member_id=? AND kind='published' AND version=?",
        (member_id, version)).fetchone()
    if not row:
        return None
    payload = apply_now(json.loads(row["payload"]), _now())
    return {"member_id": member_id, "version": row["version"],
            "published_at": row["created_at"], "reasons": REASONS, **payload}


def reset(db) -> None:
    with _lock:
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM versions")
