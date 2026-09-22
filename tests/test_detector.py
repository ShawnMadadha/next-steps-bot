from app import detector


def test_parse_strips_code_fences():
    out = detector.parse('```json\n[{"owner": "Shawn", "action": "send the DPA", "confidence": 0.9}]\n```')
    assert out == [{"owner": "Shawn", "action": "send the DPA", "due": None, "confidence": 0.9,
                    "quote": "", "followup_draft": None}]


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
