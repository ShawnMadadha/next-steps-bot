import os

import httpx

# Pay as you go workspaces live in us-west-2. Change the region here if yours is elsewhere.
BASE_URL = "https://us-west-2.recall.ai/api/v1"


def _headers():
    key = os.environ.get("RECALL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("RECALL_API_KEY is not set in .env (restart the app after editing .env)")
    return {"Authorization": f"Token {key}"}


def _json(resp):
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(
            f"Recall {resp.request.method} {resp.request.url} returned {resp.status_code}: {resp.text}"
        )
    return resp.json()


def create_bot(meeting_url):
    # Recall calls this exact URL back. The slash before the query string is required (400 without it).
    rt_url = f"{os.environ['PUBLIC_URL'].rstrip('/')}/rt/?token={os.environ['RT_TOKEN']}"
    body = {
        "meeting_url": meeting_url,
        "bot_name": "Next Steps",
        "metadata": {"app": "next-steps"},
        "recording_config": {
            "transcript": {
                # Low latency mode sends utterances 1 to 3 seconds after they are spoken; the default mode delays them by minutes.
                "provider": {"recallai_streaming": {"mode": "prioritize_low_latency", "language_code": "en"}},
                # Without this the low latency provider attributes every utterance to the host.
                "diarization": {"use_separate_streams_when_available": True},
            },
            "realtime_endpoints": [{"type": "webhook", "url": rt_url, "events": ["transcript.data"]}],
        },
    }
    return _json(httpx.post(f"{BASE_URL}/bot/", json=body, headers=_headers(), timeout=30))


def get_bot(bot_id):
    return _json(httpx.get(f"{BASE_URL}/bot/{bot_id}/", headers=_headers(), timeout=30))


def download(url):
    # Download URLs are pre-signed, so no Authorization header.
    return _json(httpx.get(url, timeout=60))
