import pytest

from app import recall


def test_missing_api_key_is_a_readable_error(monkeypatch):
    monkeypatch.setenv("RECALL_API_KEY", " ")
    monkeypatch.setenv("PUBLIC_URL", "https://example.test")
    monkeypatch.setenv("RT_TOKEN", "t")
    with pytest.raises(RuntimeError, match="RECALL_API_KEY"):
        recall.create_bot("https://meet.google.com/abc-defg-hij")
