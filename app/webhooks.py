import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app import detector, recall, store
from app.realtime import find_match, status_for

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/recall")
async def recall_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    if not verify(request.headers, raw):
        raise HTTPException(status_code=401)
    # Payloads carry no id of their own; the Svix message id header is the only handle for dedupe.
    event_id = request.headers.get("webhook-id") or request.headers.get("svix-id")
    if not store.first_time(event_id):
        return {"ok": True, "duplicate": True}

    body = json.loads(raw)
    event = body.get("event", "")
    bot_id = body["data"]["bot"]["id"]
    if store.get_bot(bot_id) is None:
        log.info("ignoring %s for a bot this app did not create: %s", event, bot_id)
        return {"ok": True}
    log.info("webhook %s for bot %s", event, bot_id)

    if event.startswith("bot."):
        change = body["data"]["data"]
        store.add_status_change(bot_id, change["code"], change.get("sub_code"), change["updated_at"])
        if change["code"] in ("done", "fatal"):
            store.mark_ended(bot_id)  # nothing more will arrive, so the page can stop refreshing
    elif event in ("recording.done", "transcript.done"):
        store.mark_ended(bot_id)
        # Either event means the transcript can be downloaded. Whichever lands first runs the pass; the other is a no-op.
        background.add_task(post_meeting_pass, bot_id)
    return {"ok": True}


def verify(headers, raw):
    """Recall signs dashboard webhooks the Svix way: HMAC SHA256 over "id.timestamp.body" with the whsec_ secret."""
    secret = os.environ.get("RECALL_WEBHOOK_SECRET", "")
    msg_id = headers.get("webhook-id") or headers.get("svix-id")
    timestamp = headers.get("webhook-timestamp") or headers.get("svix-timestamp")
    signatures = headers.get("webhook-signature") or headers.get("svix-signature")
    if not secret.startswith("whsec_") or not (msg_id and timestamp and signatures):
        return False
    expected = sign(secret, msg_id, timestamp, raw)
    # One "v1,<sig>" per active secret; there are two for a day after a rotation.
    return any(
        hmac.compare_digest(expected, s.split(",", 1)[1])
        for s in signatures.split()
        if s.startswith("v1,")
    )


def sign(secret, msg_id, timestamp, raw):
    key = base64.b64decode(secret.removeprefix("whsec_"))
    digest = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + raw, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def post_meeting_pass(bot_id):
    """Run the detector once over the whole transcript, reconcile with the live commitments, draft follow-ups."""
    bot = store.get_bot(bot_id)
    if bot is None or bot["post_meeting_at"]:
        return
    transcript = fetch_transcript(bot)
    if transcript is None:
        return

    utterances = []
    for entry in transcript:
        words = entry.get("words") or []
        if not words:
            continue
        participant = entry.get("participant") or {}
        participant_id = -1 if participant.get("id") is None else participant["id"]
        speaker = participant.get("name") or f"Participant {participant_id}"
        text = " ".join(w["text"] for w in words).strip()
        # Backfill anything the live path never saw. The unique index makes this a no-op for the rest.
        store.add_utterance(bot_id, participant_id, speaker, text, words[0]["start_timestamp"]["relative"])
        utterances.append({"speaker": speaker, "text": text})

    existing = store.list_commitments(bot_id)
    for found in detector.detect(utterances, "post_meeting"):
        match = find_match(found, existing)
        if match is not None:
            if found["followup_draft"] and not match["followup_draft"]:
                store.set_followup(match["id"], found["followup_draft"])
        elif status_for(found["confidence"]) is not None:  # same 0.5 floor as the live path
            new_id = store.add_commitment(bot_id, found, "post_meeting", found["followup_draft"])
            existing.append(store.get_commitment(new_id))

    store.confirm_proposed(bot_id)
    # Unsure ones get a draft too, so a rep can approve them from the page.
    for c in store.list_commitments(bot_id):
        if c["status"] in ("confirmed", "post_meeting", "unsure") and not c["followup_draft"]:
            store.set_followup(c["id"], template_draft(c))
    store.mark_post_meeting_done(bot_id)
    log.info("post meeting pass done for bot %s", bot_id)


def fetch_transcript(bot):
    if bot["meeting_url"].startswith("replay:"):
        # replay.py made this bot. There is no Recall bot behind it, so the fixture is the transcript.
        return json.loads(Path(bot["meeting_url"].removeprefix("replay:")).read_text())
    data = recall.get_bot(bot["id"])
    for recording in data.get("recordings") or []:
        shortcut = (recording.get("media_shortcuts") or {}).get("transcript") or {}
        url = (shortcut.get("data") or {}).get("download_url")
        if url:
            return recall.download(url)
    log.warning("bot %s has no transcript download url yet", bot["id"])
    return None


def template_draft(c):
    # The model only drafts for commitments it saw in the full transcript; live-only ones get this instead.
    due = f", {c['due']}" if c["due"] and c["due"].lower() not in c["action"].lower() else ""
    return f"Following up on our call: I'll {c['action']}{due}.\nShout if anything changes on your side."
