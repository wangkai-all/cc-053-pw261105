"""Engine rule tests: deduction order, expiry boundary, all-or-nothing,
refund ordering/caps, refund-to-expired, failed-consume protection."""
from app.engine import EventIn, apply_now, detect_conflicts, parse_ts, replay


def ev(event_id, etype, t, amount, seq=0, expires=None, ref=None, member="m1"):
    return EventIn(event_id=event_id, member_id=member, type=etype,
                   occurred_at=t, seq=seq, amount=amount,
                   expires_at=expires, ref_event_id=ref)


def batch(payload, bid):
    return next(b for b in payload["batches"] if b["batch_id"] == bid)


def event(payload, eid):
    return next(e for e in payload["events"] if e["event_id"] == eid)


def test_consume_deducts_earliest_expiry_first():
    p = replay([
        ev("c1", "claim", "2026-01-01T10:00:00Z", 100, expires="2026-01-03T00:00:00Z"),
        ev("c2", "claim", "2026-01-01T11:00:00Z", 100, expires="2026-01-02T00:00:00Z"),
        ev("s1", "consume", "2026-01-01T12:00:00Z", 60),
    ])
    # c2 expires earlier even though it was claimed later
    assert batch(p, "c2")["consumed"] == 60
    assert batch(p, "c1")["consumed"] == 0
    assert p["totals"] == {"remaining": 140, "consumed": 60, "expired": 0}


def test_consume_tie_breaks_by_claim_time_then_batch_id():
    p = replay([
        ev("b9", "claim", "2026-01-01T10:00:00Z", 50, expires="2026-01-05T00:00:00Z"),
        ev("b1", "claim", "2026-01-01T10:00:00Z", 50, expires="2026-01-05T00:00:00Z"),
        ev("s1", "consume", "2026-01-01T12:00:00Z", 30),
    ])
    # same expiry and same claim time -> smaller batch_id first
    assert batch(p, "b1")["consumed"] == 30
    assert batch(p, "b9")["consumed"] == 0


def test_expiry_boundary_batch_unusable_from_expiry_moment():
    p = replay([
        ev("c1", "claim", "2026-01-01T10:00:00Z", 100, expires="2026-01-02T12:00:00Z"),
        # consume exactly at the expiry moment: batch no longer usable
        ev("s1", "consume", "2026-01-02T12:00:00Z", 50),
    ])
    assert event(p, "s1")["result"] == "failed"
    assert event(p, "s1")["reason"] == "insufficient_balance"
    assert batch(p, "c1")["expired"] == 100
    assert batch(p, "c1")["consumed"] == 0


def test_insufficient_balance_deducts_nothing():
    p = replay([
        ev("c1", "claim", "2026-01-01T10:00:00Z", 40, expires="2026-01-05T00:00:00Z"),
        ev("c2", "claim", "2026-01-01T10:00:00Z", 40, expires="2026-01-06T00:00:00Z"),
        ev("s1", "consume", "2026-01-02T00:00:00Z", 81),
    ])
    assert event(p, "s1")["result"] == "failed"
    assert p["totals"]["remaining"] == 80
    assert p["totals"]["consumed"] == 0


def test_consume_spanning_expiry_moment():
    p = replay([
        ev("c1", "claim", "2026-01-01T10:00:00Z", 100, expires="2026-01-01T12:00:00Z"),
        ev("c2", "claim", "2026-01-01T10:05:00Z", 100, expires="2026-01-01T20:00:00Z"),
        ev("s1", "consume", "2026-01-01T11:00:00Z", 60),   # all from c1
        ev("s2", "consume", "2026-01-01T13:00:00Z", 30),   # c1 expired -> c2
    ])
    assert batch(p, "c1")["consumed"] == 60
    assert batch(p, "c1")["expired"] == 40
    assert batch(p, "c2")["consumed"] == 30
    assert event(p, "s2")["lines"] == [
        {"batch_id": "c2", "amount": 30, "refunded": 0, "to": "consumed"}]


def test_partial_refund_restores_in_original_deduction_order():
    p = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 100, expires="2026-01-01T18:00:00Z"),
        ev("c2", "claim", "2026-01-01T09:00:00Z", 100, expires="2026-01-01T23:00:00Z"),
        ev("s1", "consume", "2026-01-01T10:00:00Z", 150),  # 100 c1 + 50 c2
        ev("r1", "refund", "2026-01-01T11:00:00Z", 120, ref="s1"),
        ev("r2", "refund", "2026-01-01T12:00:00Z", 30, ref="s1"),
    ])
    assert event(p, "r1")["lines"] == [
        {"batch_id": "c1", "amount": 100, "to": "remaining"},
        {"batch_id": "c2", "amount": 20, "to": "remaining"},
    ]
    # c1 line fully refunded; r2 restores the rest of the c2 line
    assert event(p, "r2")["lines"] == [
        {"batch_id": "c2", "amount": 30, "to": "remaining"}]
    assert p["totals"]["remaining"] == 200  # 200 claimed, 150 net consumed... all refunded
    assert p["totals"]["consumed"] == 0
    assert event(p, "s1")["refunded_total"] == 150


def test_refund_total_may_not_exceed_original_consume():
    p = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 100, expires="2026-01-02T00:00:00Z"),
        ev("s1", "consume", "2026-01-01T09:00:00Z", 60),
        ev("r1", "refund", "2026-01-01T10:00:00Z", 40, ref="s1"),
        ev("r2", "refund", "2026-01-01T11:00:00Z", 30, ref="s1"),  # 40+30 > 60
    ])
    assert event(p, "r2")["result"] == "failed"
    assert event(p, "r2")["reason"] == "exceeds_original"
    assert p["totals"]["remaining"] == 80  # 40 + 40 refunded


def test_refund_to_expired_batch_is_recorded_as_expired():
    p = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 50, expires="2026-01-01T10:30:00Z"),
        ev("c2", "claim", "2026-01-01T08:00:00Z", 100, expires="2026-01-02T00:00:00Z"),
        ev("s1", "consume", "2026-01-01T09:00:00Z", 120),  # 50 c1 + 70 c2
        ev("r1", "refund", "2026-01-01T11:00:00Z", 80, ref="s1"),
    ])
    # c1 expired at 10:30 < refund time: its 50 restored as expired
    assert event(p, "r1")["lines"] == [
        {"batch_id": "c1", "amount": 50, "to": "expired"},
        {"batch_id": "c2", "amount": 30, "to": "remaining"},
    ]
    assert batch(p, "c1")["expired"] == 50
    assert batch(p, "c1")["remaining"] == 0
    assert batch(p, "c2")["remaining"] == 60


def test_failed_consume_cannot_be_refunded():
    p = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 30, expires="2026-01-02T00:00:00Z"),
        ev("s1", "consume", "2026-01-01T09:00:00Z", 50),   # fails
        ev("r1", "refund", "2026-01-01T10:00:00Z", 20, ref="s1"),
        ev("r2", "refund", "2026-01-01T10:00:00Z", 20, ref="nope"),
    ])
    assert event(p, "r1")["reason"] == "consume_failed"
    assert event(p, "r2")["reason"] == "reference_not_found"
    assert p["totals"]["remaining"] == 30


def test_events_replay_by_time_then_seq_not_import_order():
    p = replay([
        ev("s1", "consume", "2026-01-01T10:00:00Z", 70, seq=2),
        ev("c1", "claim", "2026-01-01T10:00:00Z", 100, seq=1,
           expires="2026-01-02T00:00:00Z"),
    ])
    # same timestamp: seq 1 (claim) replays before seq 2 (consume)
    assert event(p, "s1")["result"] == "success"
    assert p["totals"]["remaining"] == 30


def test_apply_now_moves_remaining_to_expired():
    p = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 100, expires="2026-01-01T12:00:00Z"),
        ev("s1", "consume", "2026-01-01T09:00:00Z", 40),
    ])
    view = apply_now(p, parse_ts("2026-01-01T13:00:00Z"))
    assert view["totals"] == {"remaining": 0, "consumed": 40, "expired": 60}
    assert view["curve"][-1]["kind"] == "now"
    assert view["curve"][-1]["balance"] == 0
    # original snapshot untouched
    assert p["totals"]["remaining"] == 60


def test_detect_conflicts_flags_regressions_only():
    pub = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 100, expires="2026-01-02T00:00:00Z"),
        ev("s1", "consume", "2026-01-01T09:00:00Z", 40),
        ev("r1", "refund", "2026-01-01T10:00:00Z", 10, ref="s1"),
    ])
    draft = replay([
        ev("c1", "claim", "2026-01-01T08:00:00Z", 100, expires="2026-01-02T00:00:00Z"),
        ev("s0", "consume", "2026-01-01T08:30:00Z", 80),   # late consume
        ev("s1", "consume", "2026-01-01T09:00:00Z", 40),   # now fails
        ev("r1", "refund", "2026-01-01T10:00:00Z", 10, ref="s1"),  # now invalid
    ])
    conflicts = detect_conflicts(pub, draft)
    kinds = {(c["event_id"], c["kind"]) for c in conflicts}
    assert kinds == {("s1", "consume_now_insufficient"),
                     ("r1", "refund_now_invalid")}
