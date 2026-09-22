import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import realtime, store
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "fixtures" / "call.json").read_text())

# A short two person call the tests control, written in Recall's transcript download schema.
CALL = [
    ("Shawn", 0.0, "Hi Priya, thanks for making time today. Let me pull up the proposal we sent over last week."),
    ("Priya", 7.4, "Sure. We looked at it and the pricing mostly works, but legal wants a few changes to the data processing terms."),
    ("Shawn", 15.1, "That's fine. I'll send the redlined DPA to your legal team by Thursday."),
    ("Priya", 21.0, "Great. I will get their comments back to you by end of next week."),
    ("Priya", 33.2, "Around forty, mostly the sales team. We'll need SSO before anyone logs in."),
    ("Shawn", 39.9, "We support Okta and Azure AD. I'll share the SSO setup guide with your IT lead tomorrow."),
    ("Shawn", 65.6, "Actually, let me revise that. I'll send the DPA by Wednesday instead so legal has more time."),
    ("Priya", 73.0, "Even better. I'll loop in our procurement team so the order form isn't held up."),
]


def make_call(path, lines=CALL):
    people = {}
    entries = []
    for speaker, start, text in lines:
        pid = people.setdefault(speaker, 100 + len(people))
        words, t = [], start
        for w in text.split():
            words.append({"text": w, "start_timestamp": {"relative": round(t, 2)}, "end_timestamp": {"relative": round(t + 0.3, 2)}})
            t += 0.4
        entries.append({"participant": {"id": pid, "name": speaker, "is_host": pid == 100, "platform": None,
                                        "extra_data": {}, "email": None},
                        "language_code": "en", "words": words})
    Path(path).write_text(json.dumps(entries))
    return entries


def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("RT_TOKEN", "t")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.8")
    store.init()
    store.add_bot("bot-1", "replay:test", "Replay")


def entry_event(entry, bot_id="bot-1"):
    return {
        "event": "transcript.data",
        "data": {
            "data": {"words": entry["words"], "language_code": "en", "participant": entry["participant"]},
            "realtime_endpoint": {"id": "e", "metadata": {}},
            "transcript": {"id": "t", "metadata": {}},
            "recording": {"id": "r", "metadata": {}},
            "bot": {"id": bot_id, "metadata": {}},
        },
    }


def event(text, speaker="Shawn", participant_id=100, start=1.0, bot_id="bot-1"):
    words = []
    t = start
    for w in text.split():
        words.append({"text": w, "start_timestamp": {"relative": t}, "end_timestamp": {"relative": t + 0.3}})
        t += 0.4
    entry = {"words": words, "participant": {"id": participant_id, "name": speaker, "is_host": True, "platform": None, "extra_data": {}}}
    return entry_event(entry, bot_id)


def replay_module():
    spec = importlib.util.spec_from_file_location("replay", ROOT / "scripts" / "replay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_has_recall_transcript_shape():
    assert isinstance(FIXTURE, list) and FIXTURE
    for entry in FIXTURE:
        assert "id" in entry["participant"] and "name" in entry["participant"]
        assert entry["words"][0]["start_timestamp"]["relative"] >= 0


def test_replay_splits_a_speaker_turn_on_pauses(tmp_path):
    replay = replay_module()
    entry = make_call(tmp_path / "one.json", [("Shawn", 0.0, "I'll send the docs in about twenty minutes")])[0]
    entry["words"][5]["start_timestamp"]["relative"] += 6.0  # a six second pause before "about"
    for w in entry["words"][5:]:
        w["end_timestamp"]["relative"] += 6.0
    parts = replay.split_on_pauses(entry)
    assert [" ".join(w["text"] for w in p["words"]) for p in parts] == ["I'll send the docs in", "about twenty minutes"]
    assert all(p["participant"] == entry["participant"] for p in parts)
    assert len(replay.split_on_pauses(make_call(tmp_path / "two.json", CALL[:1])[0])) == 1
    assert sum(len(replay.split_on_pauses(e)) for e in FIXTURE) >= len(FIXTURE)


def test_bad_token_is_rejected(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/rt/?token=wrong", json=event("hello")).status_code == 401
        assert client.post("/rt", json=event("hello")).status_code == 401


def test_retried_event_is_stored_once(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    body = entry_event(FIXTURE[0])
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
    existing = [{"owner": "Shawn", "action": "send the signed order form", "due": "Thursday"}]
    assert realtime.find_match({"owner": "shawn", "action": "send signed order form", "due": None}, existing) is existing[0]
    assert realtime.find_match({"owner": "Shawn", "action": "send the signed order form", "due": "by thursday"}, existing) is existing[0]
    assert realtime.find_match({"owner": "Shawn", "action": "send the signed order form", "due": "Wednesday"}, existing) is None
    assert realtime.find_match({"owner": "Priya", "action": "send the signed order form", "due": "Thursday"}, existing) is None
    assert realtime.find_match({"owner": "Shawn", "action": "book a reference call", "due": None}, existing) is None
    short = [{"owner": "Shawn", "action": "send the MSA", "due": "Wednesday"}]
    assert realtime.find_match({"owner": "Shawn", "action": "send the MSA to your legal team", "due": "Wednesday"}, short) is short[0]
    assert realtime.find_match({"owner": "Shawn", "action": "send the MSA to your legal team", "due": "Thursday"}, short) is None


def test_near_duplicate_commitment_is_not_added_twice(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    realtime.handle_event(event("I'll send the signed order form tomorrow.", start=1.0))
    realtime.handle_event(event("I'll send the signed order form tomorrow, promise.", start=9.0))
    realtime.handle_event(event("I'll book a reference call next week.", start=15.0))
    actions = [r["action"] for r in store.list_commitments("bot-1")]
    assert actions == ["send the signed order form tomorrow", "book a reference call next week"]
