import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import realtime, store
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "fixtures" / "call.json").read_text())


def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("RT_TOKEN", "t")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.8")
    store.init()
    store.add_bot("bot-1", "replay:test", "Replay")


def event(text, speaker="Shawn", participant_id=100, start=1.0, bot_id="bot-1"):
    words = []
    t = start
    for w in text.split():
        words.append({"text": w, "start_timestamp": {"relative": t}, "end_timestamp": {"relative": t + 0.3}})
        t += 0.4
    return {
        "event": "transcript.data",
        "data": {
            "data": {
                "words": words,
                "language_code": "en",
                "participant": {"id": participant_id, "name": speaker, "is_host": True, "platform": None, "extra_data": {}},
            },
            "realtime_endpoint": {"id": "e", "metadata": {}},
            "transcript": {"id": "t", "metadata": {}},
            "recording": {"id": "r", "metadata": {}},
            "bot": {"id": bot_id, "metadata": {}},
        },
    }


def test_fixture_has_recall_transcript_shape():
    assert isinstance(FIXTURE, list) and FIXTURE
    for entry in FIXTURE:
        assert "id" in entry["participant"] and "name" in entry["participant"]
        assert entry["words"][0]["start_timestamp"]["relative"] >= 0


def test_bad_token_is_rejected(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/rt/?token=wrong", json=event("hello")).status_code == 401
        assert client.post("/rt", json=event("hello")).status_code == 401


def test_retried_event_is_stored_once(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    first = FIXTURE[0]
    body = {
        "event": "transcript.data",
        "data": {
            "data": {"words": first["words"], "language_code": "en", "participant": first["participant"]},
            "realtime_endpoint": {"id": "e", "metadata": {}},
            "transcript": {"id": "t", "metadata": {}},
            "recording": {"id": "r", "metadata": {}},
            "bot": {"id": "bot-1", "metadata": {}},
        },
    }
    with TestClient(app) as client:
        assert client.post("/rt/?token=t", json=body).status_code == 200
        assert client.post("/rt/?token=t", json=body).status_code == 200
    assert len(store.list_utterances("bot-1")) == 1


def test_cue_gate_words():
    assert realtime.detector.has_cue("I'll send the signed order form by Thursday")
    assert realtime.detector.has_cue("Let me check with legal")
    assert realtime.detector.has_cue("I’ll get you the numbers")
    assert not realtime.detector.has_cue("The pricing mostly works for us")


def test_only_cue_utterances_reach_the_detector(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    calls = []
    real_detect = realtime.detector.detect
    monkeypatch.setattr(realtime.detector, "detect", lambda u, m: calls.append(m) or real_detect(u, m))
    realtime.handle_event(event("The pricing mostly works for us.", start=1.0))
    assert calls == []
    realtime.handle_event(event("I'll send the contract tomorrow.", start=5.0))
    assert calls == ["live"]
    rows = store.list_commitments("bot-1")
    assert [(r["owner"], r["status"], r["due"]) for r in rows] == [("Shawn", "proposed", "tomorrow")]


def test_unsure_band_and_unknown_bot(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    realtime.handle_event(event("Let me pull up the proposal.", start=1.0))
    assert [r["status"] for r in store.list_commitments("bot-1")] == ["unsure"]
    realtime.handle_event(event("I'll send it tomorrow.", start=2.0, bot_id="nobody"))
    assert store.list_utterances("nobody") == []


def test_fuzzy_match():
    existing = [{"owner": "Shawn", "action": "send the signed order form"}]
    assert realtime.find_match({"owner": "shawn", "action": "send signed order form"}, existing) is existing[0]
    assert realtime.find_match({"owner": "Priya", "action": "send the signed order form"}, existing) is None
    assert realtime.find_match({"owner": "Shawn", "action": "book a reference call"}, existing) is None


def test_near_duplicate_commitment_is_not_added_twice(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    realtime.handle_event(event("I'll send the signed order form tomorrow.", start=1.0))
    realtime.handle_event(event("I'll send the signed order form tomorrow, promise.", start=9.0))
    realtime.handle_event(event("I'll book a reference call next week.", start=15.0))
    actions = [r["action"] for r in store.list_commitments("bot-1")]
    assert actions == ["send the signed order form tomorrow", "book a reference call next week"]
