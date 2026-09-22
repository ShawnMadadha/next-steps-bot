"""Replay fixtures/call.json into a running app as transcript.data events, paced like the real call.

    .venv/bin/python scripts/replay.py [--url http://localhost:8000] [--speed 4] [--fixture fixtures/call.json]

There is no Recall bot behind a replay, so the bot row is written straight into the sqlite file.
Every utterance then goes through the real /rt path over HTTP. Watch it at /bots/<bot id>.

fixtures/call.json is a hand written placeholder until scripts/make_fixture.py is run on a real call.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402

ENV = dotenv_values(ROOT / ".env")


def transcript_event(bot_id, entry):
    return {
        "event": "transcript.data",
        "data": {
            "data": {
                "words": entry["words"],
                "language_code": entry.get("language_code", "en"),
                "participant": entry["participant"],
            },
            "realtime_endpoint": {"id": "replay", "metadata": {}},
            "transcript": {"id": "replay", "metadata": {}},
            "recording": {"id": "replay", "metadata": {}},
            "bot": {"id": bot_id, "metadata": {"app": "next-steps"}},
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--speed", type=float, default=1.0, help="4 plays the call four times faster")
    parser.add_argument("--fixture", default=str(ROOT / "fixtures" / "call.json"))
    parser.add_argument("--bot-id", default=f"replay-{int(time.time())}")
    args = parser.parse_args()

    entries = json.loads(Path(args.fixture).read_text())
    store.init()
    store.add_bot(args.bot_id, f"replay:{args.fixture}", "Replay")
    print(f"watch {args.url}/bots/{args.bot_id}")

    rt = f"{args.url}/rt/?token={ENV.get('RT_TOKEN', '')}"
    previous = None
    for entry in entries:
        start = entry["words"][0]["start_timestamp"]["relative"]
        if previous is not None:
            time.sleep(min(max(start - previous, 0) / args.speed, 8))
        previous = start
        resp = httpx.post(rt, json=transcript_event(args.bot_id, entry), timeout=30)
        text = " ".join(w["text"] for w in entry["words"])
        print(f"[{start:6.1f}s] {entry['participant']['name']}: {text}  -> {resp.status_code}")
        resp.raise_for_status()


if __name__ == "__main__":
    main()
