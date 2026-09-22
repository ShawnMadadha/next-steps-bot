from types import SimpleNamespace

from app import detector


def test_parse_strips_code_fences():
    out = detector.parse('```json\n[{"owner": "Shawn", "action": "send the DPA", "confidence": 0.9}]\n```')
    assert out == [{"owner": "Shawn", "owner_id": None, "action": "send the DPA", "due": None, "confidence": 0.9,
                    "quote": "", "followup_draft": None}]
    assert detector.parse('[{"owner": "Shawn", "owner_id": 100, "action": "send the DPA", "confidence": 0.9}]')[0]["owner_id"] == 100


def test_parse_malformed_responses_return_nothing():
    assert detector.parse("Sure! Here are the commitments I found:") == []
    assert detector.parse('[{"owner": "Shawn"}]') == []
    assert detector.parse('{"commitments": []}') == []
    assert detector.parse('[{"owner": "Shawn", "action": "x", "confidence": "high"}]') == []
    assert detector.parse("") == []


def test_dry_run_stub(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    utterances = [
        {"speaker": "Shawn", "text": "I'll send the redlined DPA to your legal team by Thursday."},
        {"speaker": "Priya", "text": "The pricing mostly works."},
        {"speaker": "Shawn", "text": "Let me check with finance."},
    ]
    live = detector.detect(utterances, "live")
    assert [(c["owner"], c["due"], c["confidence"]) for c in live] == [("Shawn", "by Thursday", 0.9), ("Shawn", None, 0.6)]
    assert live[0]["followup_draft"] is None
    post = detector.detect(utterances, "post_meeting")
    assert post[0]["followup_draft"].count("\n") == 1


def test_stub_skips_requirements_and_meta_talk(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    utterances = [
        {"speaker": "Priya", "text": "We'll need SSO before anyone logs in."},
        {"speaker": "Shawn", "text": "Let me check that with finance."},
        {"speaker": "Shawn", "text": "Actually, let me revise that. I'll send the deck tomorrow."},
    ]
    assert [c["action"] for c in detector.detect(utterances, "live")] == ["send the deck tomorrow"]


def test_stub_drops_the_earlier_version_of_a_revised_promise(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    utterances = [
        {"speaker": "Shawn", "text": "I'll send the redlined DPA to your legal team by Thursday."},
        {"speaker": "Shawn", "text": "I'll set up a reference call next week."},
        {"speaker": "Priya", "text": "I'll send the DPA to our CFO."},
        {"speaker": "Shawn", "text": "Actually, I'll send the DPA by Wednesday instead."},
    ]
    live = [c["action"] for c in detector.detect(utterances, "live")]
    assert live == ["send the redlined DPA to your legal team by Thursday", "set up a reference call next week",
                    "send the DPA to our CFO", "send the DPA by Wednesday instead"]
    post = [c["action"] for c in detector.detect(utterances, "post_meeting")]
    assert post == ["set up a reference call next week", "send the DPA to our CFO", "send the DPA by Wednesday instead"]


def test_post_meeting_prompt_lists_the_live_commitments(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='[{"owner": "Shawn", "action": "send the docs", "confidence": 0.9}]')])

    monkeypatch.setattr(detector.anthropic, "Anthropic", lambda: SimpleNamespace(messages=FakeMessages()))
    utterances = [{"speaker": "Shawn", "text": "I'll send the docs tomorrow."}]
    live = [{"owner": "Shawn", "owner_id": 100, "action": "send the docs", "due": "tomorrow"}, {"owner": "Priya", "action": "loop in procurement", "due": None}]
    out = detector.detect(utterances, "post_meeting", known=live)
    assert out[0]["action"] == "send the docs"
    content = calls[0]["messages"][0]["content"]
    assert content.startswith("Mode: post_meeting\n\nTranscript:\nShawn: I'll send the docs tomorrow.")
    assert "- Shawn [100] | send the docs | tomorrow" in content and "- Priya | loop in procurement | no due" in content
    assert "Commitments the live pass already found" in calls[0]["system"] or "live pass already found" in calls[0]["system"]
    detector.detect(utterances, "live")
    assert "already found" not in calls[1]["messages"][0]["content"]


def test_stub_reads_the_participant_id_from_the_speaker_label(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    out = detector.detect([{"speaker": "Shawn Madadha [200]", "text": "I'll send the deck tomorrow."}], "live")
    assert (out[0]["owner"], out[0]["owner_id"]) == ("Shawn Madadha", 200)
    out = detector.detect([{"speaker": "Priya", "text": "I'll send the deck tomorrow."}], "live")
    assert (out[0]["owner"], out[0]["owner_id"]) == ("Priya", None)
