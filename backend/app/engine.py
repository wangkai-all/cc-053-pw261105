"""Pure replay engine for the membership points ledger.

Given the full event stream of one member, replays it in stable order
(occurred_at, seq, event_id) and produces a JSON-serialisable snapshot:
per-batch remaining / consumed / expired quantities, per-event results and
a balance curve. Everything about a member's ledger is derived here, so a
"replay version" is fully determined by the set of applied events.

Rules implemented:
- a batch is usable strictly before its expires_at (到期时刻起不能使用)
- consume deducts earliest-expiry first; ties broken by (claimed_at, batch_id)
- consume is all-or-nothing: insufficient balance -> nothing is deducted
- refund restores the original consume's deduction lines in order, each line
  up to (deducted - already refunded); a batch already expired at refund time
  has the restored amount recorded as expired (it does not regain validity)
- total refunds per consume may not exceed the consumed amount;
  failed consumes cannot be refunded
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class EventIn:
    event_id: str
    member_id: str
    type: str  # claim | consume | refund
    occurred_at: str
    seq: int
    amount: int
    expires_at: str | None = None
    ref_event_id: str | None = None


def sort_key(e: EventIn):
    return (parse_ts(e.occurred_at), e.seq, e.event_id)


REASONS = {
    "insufficient_balance": "余额不足，整笔消费未扣减",
    "reference_not_found": "引用的原消费不存在",
    "consume_failed": "原消费未成功，不能退款",
    "exceeds_original": "累计退款超过原消费金额",
}

EVENT_TYPES = ("claim", "consume", "refund")


def replay(events: list[EventIn]) -> dict:
    """Replay one member's events and return the ledger snapshot."""
    evs = sorted(events, key=sort_key)
    batches: dict[str, dict] = {}
    batch_order: list[str] = []
    consumes: dict[str, dict] = {}
    out_events: list[dict] = []
    curve: list[dict] = []
    balance = 0

    def expire_due(t: datetime) -> None:
        nonlocal balance
        for bid in batch_order:
            b = batches[bid]
            if b["remaining"] > 0 and parse_ts(b["expires_at"]) <= t:
                balance -= b["remaining"]
                b["expired"] += b["remaining"]
                b["remaining"] = 0
                curve.append({
                    "t": b["expires_at"], "balance": balance,
                    "kind": "expire", "batch_id": bid,
                })

    for e in evs:
        t = parse_ts(e.occurred_at)
        expire_due(t)
        rec = {
            "event_id": e.event_id, "type": e.type, "occurred_at": iso(t),
            "seq": e.seq, "amount": e.amount, "result": "success",
            "reason": None, "lines": [],
        }
        if e.type == "claim":
            bid = e.event_id
            batches[bid] = {
                "batch_id": bid, "amount": e.amount, "claimed_at": iso(t),
                "expires_at": iso(parse_ts(e.expires_at)),
                "remaining": e.amount, "consumed": 0, "expired": 0,
            }
            batch_order.append(bid)
            balance += e.amount
            rec["expires_at"] = iso(parse_ts(e.expires_at))
            rec["lines"] = [{"batch_id": bid, "amount": e.amount, "to": "remaining"}]
        elif e.type == "consume":
            eligible = [
                batches[bid] for bid in batch_order
                if batches[bid]["remaining"] > 0
                and parse_ts(batches[bid]["expires_at"]) > t
            ]
            eligible.sort(key=lambda b: (
                parse_ts(b["expires_at"]), parse_ts(b["claimed_at"]), b["batch_id"],
            ))
            if sum(b["remaining"] for b in eligible) < e.amount:
                rec["result"] = "failed"
                rec["reason"] = "insufficient_balance"
                consumes[e.event_id] = {
                    "amount": e.amount, "result": "failed",
                    "lines": [], "refunded_total": 0,
                }
            else:
                left = e.amount
                lines = []
                for b in eligible:
                    if left <= 0:
                        break
                    take = min(b["remaining"], left)
                    b["remaining"] -= take
                    b["consumed"] += take
                    balance -= take
                    left -= take
                    lines.append({
                        "batch_id": b["batch_id"], "amount": take,
                        "refunded": 0, "to": "consumed",
                    })
                rec["lines"] = lines  # shared dicts: later refunds update them
                consumes[e.event_id] = {
                    "amount": e.amount, "result": "success",
                    "lines": lines, "refunded_total": 0,
                }
        elif e.type == "refund":
            rec["ref_event_id"] = e.ref_event_id
            c = consumes.get(e.ref_event_id or "")
            if c is None:
                rec["result"] = "failed"
                rec["reason"] = "reference_not_found"
            elif c["result"] != "success":
                rec["result"] = "failed"
                rec["reason"] = "consume_failed"
            elif c["refunded_total"] + e.amount > c["amount"]:
                rec["result"] = "failed"
                rec["reason"] = "exceeds_original"
            else:
                left = e.amount
                restores = []
                for line in c["lines"]:
                    if left <= 0:
                        break
                    take = min(line["amount"] - line["refunded"], left)
                    if take <= 0:
                        continue
                    line["refunded"] += take
                    b = batches[line["batch_id"]]
                    b["consumed"] -= take
                    if parse_ts(b["expires_at"]) <= t:
                        b["expired"] += take  # 到期批次恢复记为过期
                        restores.append({
                            "batch_id": line["batch_id"], "amount": take,
                            "to": "expired",
                        })
                    else:
                        b["remaining"] += take
                        balance += take
                        restores.append({
                            "batch_id": line["batch_id"], "amount": take,
                            "to": "remaining",
                        })
                    left -= take
                c["refunded_total"] += e.amount
                rec["lines"] = restores
        out_events.append(rec)
        curve.append({"t": iso(t), "balance": balance, "kind": e.type,
                      "event_id": e.event_id})
        expire_due(t)  # e.g. a claim already expired at its own occurred_at

    for rec in out_events:
        if rec["type"] == "consume":
            rec["refunded_total"] = consumes[rec["event_id"]]["refunded_total"]

    totals = {
        "remaining": sum(b["remaining"] for b in batches.values()),
        "consumed": sum(b["consumed"] for b in batches.values()),
        "expired": sum(b["expired"] for b in batches.values()),
    }
    return {
        "batches": [batches[bid] for bid in batch_order],
        "events": out_events,
        "curve": curve,
        "totals": totals,
        "as_of": iso(parse_ts(evs[-1].occurred_at)) if evs else None,
    }


def apply_now(payload: dict, now: datetime) -> dict:
    """Return a copy of a replay snapshot advanced to wall-clock `now`.

    The only time-driven change between events is expiry, so this moves
    remaining -> expired for batches whose expires_at <= now and extends
    the balance curve accordingly.
    """
    now = now.astimezone(timezone.utc)
    p = copy.deepcopy(payload)
    curve = sorted(p["curve"], key=lambda c: parse_ts(c["t"]))
    balance = curve[-1]["balance"] if curve else 0
    due = [
        b for b in p["batches"]
        if b["remaining"] > 0 and parse_ts(b["expires_at"]) <= now
    ]
    due.sort(key=lambda b: parse_ts(b["expires_at"]))
    for b in due:
        balance -= b["remaining"]
        b["expired"] += b["remaining"]
        b["remaining"] = 0
        curve.append({"t": b["expires_at"], "balance": balance,
                      "kind": "expire", "batch_id": b["batch_id"]})
    curve.append({"t": iso(now), "balance": balance, "kind": "now"})
    p["curve"] = curve
    p["totals"] = {
        "remaining": sum(b["remaining"] for b in p["batches"]),
        "consumed": sum(b["consumed"] for b in p["batches"]),
        "expired": sum(b["expired"] for b in p["batches"]),
    }
    p["as_of"] = iso(now)
    return p


def detect_conflicts(published: dict, draft: dict) -> list[dict]:
    """Regression check between the published replay and a recompute draft.

    A conflict is an event that succeeded in the published version but fails
    in the draft: a previously valid consume becoming insufficient, or a
    refund whose reference became invalid.
    """
    pub = {e["event_id"]: e for e in published["events"]}
    conflicts = []
    for de in draft["events"]:
        pe = pub.get(de["event_id"])
        if not pe:
            continue
        if pe["result"] == "success" and de["result"] == "failed":
            conflicts.append({
                "event_id": de["event_id"],
                "type": de["type"],
                "kind": ("consume_now_insufficient" if de["type"] == "consume"
                         else "refund_now_invalid"),
                "reason": de["reason"],
                "amount": de["amount"],
                "occurred_at": de["occurred_at"],
            })
    return conflicts
