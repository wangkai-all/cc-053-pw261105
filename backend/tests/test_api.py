"""API integration tests: import, idempotency, late-event drafts,
publish/discard, version history."""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("LEDGER_DB", db_path)
    import importlib

    import app.db as db_mod
    importlib.reload(db_mod)
    import app.store as store_mod
    importlib.reload(store_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def claim(eid, t, amount, expires, member="m1", seq=0):
    return {"event_id": eid, "member_id": member, "type": "claim",
            "occurred_at": t, "seq": seq, "amount": amount, "expires_at": expires}


def consume(eid, t, amount, member="m1", seq=0):
    return {"event_id": eid, "member_id": member, "type": "consume",
            "occurred_at": t, "seq": seq, "amount": amount}


def refund(eid, t, amount, ref, member="m1", seq=0):
    return {"event_id": eid, "member_id": member, "type": "refund",
            "occurred_at": t, "seq": seq, "amount": amount, "ref_event_id": ref}


def imp(client, events):
    r = client.post("/api/events/import", json={"events": events})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_import_and_ledger(client):
    res = imp(client, [
        claim("c1", "2026-01-01T08:00:00Z", 100, "2099-01-03T00:00:00Z"),
        consume("s1", "2026-01-01T09:00:00Z", 30),
    ])
    assert [r["status"] for r in res] == ["applied", "applied"]
    ledger = client.get("/api/members/m1/ledger").json()
    assert ledger["totals"]["remaining"] == 70
    assert ledger["totals"]["consumed"] == 30
    assert ledger["version"] == 1
    members = client.get("/api/members").json()
    assert members[0]["member_id"] == "m1"
    assert members[0]["totals"]["remaining"] == 70


def test_idempotent_import_and_content_conflict(client):
    e = claim("c1", "2026-01-01T08:00:00Z", 100, "2099-01-03T00:00:00Z")
    assert imp(client, [e])[0]["status"] == "applied"
    # identical content -> processed once
    assert imp(client, [e])[0]["status"] == "duplicate"
    # same id, changed content -> conflict
    changed = dict(e, amount=200)
    r = imp(client, [changed])[0]
    assert r["status"] == "conflict"
    ledger = client.get("/api/members/m1/ledger").json()
    assert ledger["totals"]["remaining"] == 100  # unchanged


def test_validation_error(client):
    r = imp(client, [{"event_id": "x", "member_id": "m1", "type": "consume",
                      "occurred_at": "2026-01-01T00:00:00Z", "amount": -5}])[0]
    assert r["status"] == "error"


def test_late_event_creates_draft_with_conflicts_then_publish(client):
    imp(client, [
        claim("c1", "2026-01-01T08:00:00Z", 100, "2099-01-02T00:00:00Z"),
        consume("s1", "2026-01-01T09:00:00Z", 40),
        refund("r1", "2026-01-01T10:00:00Z", 10, "s1"),
    ])
    before = client.get("/api/members/m1/ledger").json()
    assert before["totals"]["remaining"] == 70

    # late consume at 08:30 makes s1 insufficient and r1 invalid
    res = imp(client, [consume("s0", "2026-01-01T08:30:00Z", 80)])[0]
    assert res["status"] == "pending"

    # published ledger unchanged while the draft waits
    still = client.get("/api/members/m1/ledger").json()
    assert still["totals"]["remaining"] == 70
    assert still["has_draft"] is True
    assert still["pending_event_ids"] == ["s0"]

    draft = client.get("/api/members/m1/draft").json()
    assert {c["kind"] for c in draft["conflicts"]} == {
        "consume_now_insufficient", "refund_now_invalid"}
    assert draft["totals"]["remaining"] == 20  # 100 - 80
    assert draft["base"]["totals"]["remaining"] == 70

    r = client.post("/api/members/m1/draft/publish")
    assert r.status_code == 200
    after = client.get("/api/members/m1/ledger").json()
    assert after["totals"]["remaining"] == 20
    assert after["has_draft"] is False
    assert after["version"] == 2

    versions = client.get("/api/members/m1/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    v1 = client.get("/api/members/m1/versions/1").json()
    assert v1["totals"]["remaining"] == 70  # old replay version comparable


def test_late_event_without_conflict_and_discard(client):
    imp(client, [
        claim("c1", "2026-01-01T08:00:00Z", 100, "2099-01-03T00:00:00Z"),
        consume("s1", "2026-01-01T09:00:00Z", 90),
    ])
    # late claim earlier on the same day: no regression, still a draft
    res = imp(client, [claim("c0", "2026-01-01T07:00:00Z", 50,
                             "2099-01-02T00:00:00Z")])[0]
    assert res["status"] == "pending"
    draft = client.get("/api/members/m1/draft").json()
    assert draft["conflicts"] == []
    assert draft["totals"]["remaining"] == 60  # 150 - 90

    r = client.post("/api/members/m1/draft/discard")
    assert r.status_code == 200
    ledger = client.get("/api/members/m1/ledger").json()
    assert ledger["totals"]["remaining"] == 10
    assert ledger["has_draft"] is False
    assert client.get("/api/members/m1/draft").status_code == 404


def test_events_during_open_draft_join_the_draft(client):
    imp(client, [claim("c1", "2026-01-01T08:00:00Z", 100, "2099-01-03T00:00:00Z")])
    imp(client, [consume("s1", "2026-01-01T09:00:00Z", 10)])
    imp(client, [consume("s0", "2026-01-01T08:30:00Z", 10)])  # late -> draft
    # not late vs published, but a draft is open -> joins the draft
    res = imp(client, [consume("s2", "2026-01-01T10:00:00Z", 10)])[0]
    assert res["status"] == "pending"
    ledger = client.get("/api/members/m1/ledger").json()
    assert ledger["totals"]["remaining"] == 90  # only c1 - s1 published
    client.post("/api/members/m1/draft/publish")
    ledger = client.get("/api/members/m1/ledger").json()
    assert ledger["totals"]["remaining"] == 70


def test_members_isolated(client):
    imp(client, [claim("c1", "2026-01-01T08:00:00Z", 100,
                       "2099-01-03T00:00:00Z", member="a")])
    imp(client, [claim("c2", "2026-01-01T08:00:00Z", 50,
                       "2099-01-03T00:00:00Z", member="b")])
    members = {m["member_id"]: m for m in client.get("/api/members").json()}
    assert members["a"]["totals"]["remaining"] == 100
    assert members["b"]["totals"]["remaining"] == 50


def test_reset(client):
    imp(client, [claim("c1", "2026-01-01T08:00:00Z", 100, "2099-01-03T00:00:00Z")])
    client.post("/api/demo/reset")
    assert client.get("/api/members").json() == []
