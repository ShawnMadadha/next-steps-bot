"""Replay fixtures/call.json into a running app as transcript.data events, paced like the real call.

    .venv/bin/python scripts/replay.py [--url http://localhost:8000] [--speed 4] [--finish]

There is no Recall bot behind a replay, so the bot row is written straight into the sqlite file.
Every utterance then goes through the real /rt path over HTTP. Watch it at /bots/<bot id>.
--finish also sends signed dashboard webhooks (bot status changes and recording.done) so the
post-meeting pass runs, which needs RECALL_WEBHOOK_SECRET in .env (any whsec_ value works locally).

fixtures/call.json is a hand written placeholder until scripts/make_fixture.py is run on a real call.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import store, webhooks  # noqa: E402

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


def status_event(bot_id, event, code, sub_code=None):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {"event": event, "data": {"data": {"code": code, "sub_code": sub_code, "updated_at": now},
                                     "bot": {"id": bot_id, "metadata": {"app": "next-steps"}}}}
    if not event.startswith("bot."):
        body["data"]["recording"] = {"id": "replay", "metadata": {}}
    return body


def post_signed(url, body, secret):
    raw = json.dumps(body).encode()
    msg_id = f"msg_replay_{time.time_ns()}"
    timestamp = str(int(time.time()))
    headers = {
        "content-type": "application/json",
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{webhooks.sign(secret, msg_id, timestamp, raw)}",
    }
    resp = httpx.post(f"{url}/webhooks/recall", content=raw, headers=headers, timeout=30)
    print(f"webhook {body['event']} -> {resp.status_code}")
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--speed", type=float, default=1.0, help="4 plays the call four times faster")
    parser.add_argument("--fixture", default=str(ROOT / "fixtures" / "call.json"))
    parser.add_argument("--bot-id", default=f"replay-{int(time.time())}")
    parser.add_argument("--finish", action="store_true", help="also send the end of call webhooks")
    args = parser.parse_args()

    secret = ENV.get("RECALL_WEBHOOK_SECRET", "")
    if args.finish and not secret.startswith("whsec_"):
        sys.exit("--finish needs RECALL_WEBHOOK_SECRET in .env (any whsec_ value works for a local replay)")

    entries = json.loads(Path(args.fixture).read_text())
    store.init()
    store.add_bot(args.bot_id, f"replay:{args.fixture}", "Replay")
    print(f"watch {args.url}/bots/{args.bot_id}")

    if args.finish:
        post_signed(args.url, status_event(args.bot_id, "bot.joining_call", "joining_call"), secret)
        post_signed(args.url, status_event(args.bot_id, "bot.in_call_recording", "in_call_recording"), secret)

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

    if args.finish:
        time.sleep(1)  # let the last live detection finish before the post-meeting pass reconciles
        post_signed(args.url, status_event(args.bot_id, "bot.call_ended", "call_ended", "call_ended_by_host"), secret)
        post_signed(args.url, status_event(args.bot_id, "bot.done", "done"), secret)
        post_signed(args.url, status_event(args.bot_id, "recording.done", "done"), secret)


if __name__ == "__main__":
    main()
