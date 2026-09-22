import json
import time

from fastapi.testclient import TestClient

from app import actions, detector, realtime, store, webhooks
from app.main import app
from tests.test_realtime import entry_event, event, fresh, make_call

SECRET = "whsec_dGVzdC1zZWNyZXQtdGVzdC1zZWNyZXQtMTIzNA=="


def status_body(event_name, code, sub_code=None, bot_id="bot-1"):
    body = {"event": event_name, "data": {"data": {"code": code, "sub_code": sub_code, "updated_at": "2026-09-21T17:05:00Z"},
                                          "bot": {"id": bot_id, "metadata": {}}}}
    if not event_name.startswith("bot."):
        body["data"]["recording"] = {"id": "rec", "metadata": {}}
    return body


def post_signed(client, body, secret=SECRET, msg_id=None, prefix="webhook"):
    raw = json.dumps(body).encode()
    msg_id = msg_id or f"msg_{time.time_ns()}"
    ts = str(int(time.time()))
    headers = {"content-type": "application/json", f"{prefix}-id": msg_id, f"{prefix}-timestamp": ts,
               f"{prefix}-signature": f"v1,{webhooks.sign(secret, msg_id, ts, raw)}"}
    return client.post("/webhooks/recall", content=raw, headers=headers)


def test_unsigned_or_badly_signed_requests_are_rejected(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    body = status_body("bot.in_call_recording", "in_call_recording")
    with TestClient(app) as client:
        assert client.post("/webhooks/recall", json=body).status_code == 401
        assert post_signed(client, body, secret="whsec_d3Jvbmctc2VjcmV0LXdyb25nLXNlY3JldC0xMjM0").status_code == 401
        raw = json.dumps(body).encode()
        sig = webhooks.sign(SECRET, "msg_x", "1", raw)
        tampered = client.post("/webhooks/recall", content=raw + b" ", headers={
            "webhook-id": "msg_x", "webhook-timestamp": "1", "webhook-signature": f"v1,{sig}"})
        assert tampered.status_code == 401
    assert store.list_status_changes("bot-1") == []


def test_no_secret_configured_rejects_everything(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", "")
    with TestClient(app) as client:
        assert post_signed(client, status_body("bot.done", "done"), secret=SECRET).status_code == 401


def test_status_changes_are_stored_once_per_webhook_id(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    with TestClient(app) as client:
        body = status_body("bot.in_call_recording", "in_call_recording")
        assert post_signed(client, body, msg_id="msg_1").status_code == 200
        assert post_signed(client, body, msg_id="msg_1").json() == {"ok": True, "duplicate": True}
        # Legacy workspaces send svix-* headers and a second signature after a rotation.
        assert post_signed(client, status_body("bot.call_ended", "call_ended", "bot_kicked_from_call"),
                           prefix="svix").status_code == 200
        assert post_signed(client, status_body("bot.done", "done", bot_id="someone-elses-bot")).status_code == 200
    rows = store.list_status_changes("bot-1")
    assert [(r["code"], r["sub_code"]) for r in rows] == [("in_call_recording", ""), ("call_ended", "bot_kicked_from_call")]
    with TestClient(app) as client:  # the same status again, from the bot object, is not stored twice
        assert post_signed(client, body).status_code == 200
    assert len(store.list_status_changes("bot-1")) == 2
    assert store.get_bot("bot-1")["ended"] == 0
    assert store.list_status_changes("someone-elses-bot") == []


def test_bot_done_marks_the_bot_ended(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    with TestClient(app) as client:
        post_signed(client, status_body("bot.fatal", "fatal", "meeting_not_found"))
    assert store.get_bot("bot-1")["ended"] == 1


def test_post_meeting_pass_reconciles_live_commitments(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    entries = make_call(tmp_path / "call.json")
    store.add_bot("replay-1", f"replay:{tmp_path / 'call.json'}", "Replay")
    store.add_bot("replay-2", f"replay:{tmp_path / 'call.json'}", "Replay")
    # Three utterances arrive live (unsure, proposed, proposed); the rest of the call is "missed" live.
    realtime.handle_event(entry_event(entries[0], bot_id="replay-1"))
    realtime.handle_event(entry_event(entries[2], bot_id="replay-1"))  # DPA by Thursday, revised to Wednesday later
    realtime.handle_event(entry_event(entries[5], bot_id="replay-1"))  # SSO setup guide tomorrow
    realtime.handle_event(event("Let me think about the timeline.", start=99.0, bot_id="replay-1"))  # not in the transcript
    assert [c["status"] for c in store.list_commitments("replay-1")] == ["unsure", "proposed", "proposed", "unsure"]

    with TestClient(app) as client:
        assert post_signed(client, status_body("recording.done", "done", bot_id="replay-1")).status_code == 200
        before = len(store.list_commitments("replay-1"))
        assert post_signed(client, status_body("transcript.done", "done", bot_id="replay-1")).status_code == 200
        # A bot whose live path saw nothing gets its transcript from the download.
        assert post_signed(client, status_body("recording.done", "done", bot_id="replay-2")).status_code == 200

    bot = store.get_bot("replay-1")
    assert bot["ended"] == 1 and bot["post_meeting_at"]
    rows = store.list_commitments("replay-1")
    assert len(rows) == before  # the second event was a no-op
    assert rows[0]["status"] == "unsure"
    assert (rows[1]["status"], rows[1]["sent_via"], rows[1]["followup_draft"]) == ("superseded", None, None)
    assert (rows[2]["status"], rows[2]["sent_via"]) == ("sent", "auto")  # confirmed, then sent by the gate
    assert (rows[3]["status"], rows[3]["followup_draft"]) == ("superseded", None)  # unsure, and the full pass never saw it
    assert {r["status"] for r in rows[4:]} == {"post_meeting"}
    assert all(r["followup_draft"] for r in rows if r["status"] != "superseded")
    assert len(store.list_utterances("replay-1")) == 4  # the live transcript stays as it was
    assert len(store.list_utterances("replay-2")) == len(entries)


def test_template_draft_does_not_repeat_the_due_date():
    c = {"action": "send the DPA by Thursday", "due": "by Thursday"}
    assert webhooks.template_draft(c).startswith("Following up on our call: I'll send the DPA by Thursday.\n")
    c = {"action": "send the DPA", "due": "Thursday"}
    assert "send the DPA, Thursday." in webhooks.template_draft(c)


def test_revised_commitment_is_superseded_and_sends_once(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    make_call(tmp_path / "call.json")
    store.add_bot("replay-1", f"replay:{tmp_path / 'call.json'}", "Replay")
    thursday = {"owner": "Shawn", "action": "send the DPA by Thursday", "due": "Thursday", "confidence": 0.9,
                "quote": "I'll send the DPA by Thursday.", "followup_draft": None}
    wednesday = {"owner": "Shawn", "action": "send the DPA by Wednesday", "due": "Wednesday", "confidence": 0.9,
                 "quote": "Actually, I'll send the DPA by Wednesday instead.",
                 "followup_draft": "Following up on our call: I'll send the DPA by Wednesday.\nShout if anything changes."}
    monkeypatch.setattr(detector, "detect", lambda utterances, mode, **kw: [wednesday] if mode == "post_meeting" else [thursday])
    sent = []
    monkeypatch.setattr(actions, "send_followup", sent.append)

    realtime.handle_event(event("I'll send the DPA by Thursday.", bot_id="replay-1"))
    assert [(c["action"], c["status"]) for c in store.list_commitments("replay-1")] == [("send the DPA by Thursday", "proposed")]

    with TestClient(app) as client:
        assert post_signed(client, status_body("recording.done", "done", bot_id="replay-1")).status_code == 200
        rows = store.list_commitments("replay-1")
        assert [(c["action"], c["status"]) for c in rows] == [
            ("send the DPA by Thursday", "superseded"), ("send the DPA by Wednesday", "post_meeting")]
        assert rows[0]["followup_draft"] is None and rows[1]["followup_draft"]
        assert sent == []  # post_meeting waits for a rep, superseded never sends
        page = client.get("/bots/replay-1").text
        assert "superseded (revised or not found in full transcript)" in page
        assert client.post(f"/commitments/{rows[1]['id']}/approve").status_code == 200
        assert client.post(f"/commitments/{rows[0]['id']}/approve").status_code == 404
    assert [c["action"] for c in sent] == ["send the DPA by Wednesday"]
    assert store.get_commitment(rows[0]["id"])["status"] == "superseded"
