import httpx
from fastapi.testclient import TestClient

from app import actions, realtime, store, webhooks
from app.main import app
from tests.test_realtime import entry_event, fresh, make_call
from tests.test_webhooks import SECRET, post_signed, status_body

COMMITMENT = {"id": 1, "owner": "Shawn", "action": "send the DPA", "due": "Thursday",
              "followup_draft": "Following up: I'll send the DPA by Thursday.\nShout if anything changes."}


def test_dry_run_prints_instead_of_posting(monkeypatch, capsys):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not post")))
    actions.send_followup(COMMITMENT)
    out = capsys.readouterr().out
    assert "dry run" in out and "Follow-up from Shawn: send the DPA, due Thursday" in out


def test_posts_the_draft_to_slack(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/abc")
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json)
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(httpx, "post", fake_post)
    actions.send_followup(COMMITMENT)
    assert sent["url"] == "https://hooks.slack.test/abc"
    assert sent["json"]["text"].endswith(COMMITMENT["followup_draft"])


def test_slack_errors_are_readable(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    try:
        actions.send_followup(COMMITMENT)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "SLACK_WEBHOOK_URL" in str(e)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/abc")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(404, text="no_service"))
    try:
        actions.send_followup(COMMITMENT)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "404" in str(e) and "no_service" in str(e)


def finished_replay_bot(client, tmp_path):
    entries = make_call(tmp_path / "call.json")
    store.add_bot("replay-1", f"replay:{tmp_path / 'call.json'}", "Replay")
    realtime.handle_event(entry_event(entries[0], bot_id="replay-1"))  # unsure
    realtime.handle_event(entry_event(entries[5], bot_id="replay-1"))  # proposed, found again by the full pass
    assert post_signed(client, status_body("recording.done", "done", bot_id="replay-1")).status_code == 200


def test_confirmed_commitments_are_sent_automatically_and_the_rest_wait(tmp_path, monkeypatch, capsys):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    with TestClient(app) as client:
        finished_replay_bot(client, tmp_path)
        rows = store.list_commitments("replay-1")
        assert (rows[0]["status"], rows[0]["sent_via"]) == ("unsure", None)
        assert (rows[1]["status"], rows[1]["sent_via"]) == ("sent", "auto")
        assert {r["status"] for r in rows[2:]} == {"post_meeting"}
        assert capsys.readouterr().out.count("[dry run] would post to Slack") == 1

        page = client.get("/bots/replay-1").text
        assert "sent automatically" in page
        assert "replay of fixtures/call.json" in page and str(tmp_path) not in page
        assert "replay of fixtures/call.json" in client.get("/").text
        assert page.count("Approve and send") == len(rows) - 1

        # A rep approves one and dismisses another; both leave the queue and the page says who acted.
        approved = client.post(f"/commitments/{rows[0]['id']}/approve", follow_redirects=False)
        assert approved.status_code == 303 and approved.headers["location"] == "/bots/replay-1"
        assert client.post(f"/commitments/{rows[2]['id']}/dismiss", follow_redirects=False).status_code == 303
        assert client.post("/commitments/999/approve").status_code == 404

        rows = store.list_commitments("replay-1")
        assert (rows[0]["status"], rows[0]["sent_via"]) == ("sent", "rep")
        assert (rows[2]["status"], rows[2]["sent_via"]) == ("dismissed", None)
        page = client.get("/bots/replay-1").text
        assert "sent by rep" in page and "dismissed" in page
        assert page.count("Approve and send") == len(rows) - 3


def test_a_failed_send_leaves_the_commitment_for_a_rep(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(webhooks.actions, "send_followup", lambda c: (_ for _ in ()).throw(RuntimeError("Slack down")))
    with TestClient(app) as client:
        finished_replay_bot(client, tmp_path)
    rows = store.list_commitments("replay-1")
    assert rows[1]["status"] == "confirmed" and rows[1]["sent_via"] is None
    assert store.get_bot("replay-1")["post_meeting_at"]
