import hmac
import logging
import os
import threading
from difflib import SequenceMatcher

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app import detector, store

log = logging.getLogger(__name__)
router = APIRouter()

WINDOW = 8          # utterances the model sees on each live call
MATCH_RATIO = 0.8   # SequenceMatcher ratio above which two actions are the same commitment

# One live detection at a time, so two events for the same bot cannot both add the same commitment.
_lock = threading.Lock()


@router.post("/rt")
@router.post("/rt/")
async def rt(request: Request, background: BackgroundTasks, token: str = ""):
    expected = os.environ.get("RT_TOKEN", "")
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401)
    body = await request.json()
    # Answer Recall right away. The model call runs after the response so it never delays the next utterance.
    background.add_task(handle_event, body)
    return {"ok": True}


def handle_event(body):
    if body.get("event") != "transcript.data":
        log.info("ignoring %s", body.get("event"))
        return
    bot_id = body["data"]["bot"]["id"]
    data = body["data"]["data"]
    words = data.get("words") or []
    if not words:
        return
    text = " ".join(w["text"] for w in words).strip()
    participant = data.get("participant") or {}
    participant_id = -1 if participant.get("id") is None else participant["id"]
    speaker = participant.get("name") or f"Participant {participant_id}"
    start_ts = words[0]["start_timestamp"]["relative"]

    if store.get_bot(bot_id) is None:
        log.warning("transcript.data for unknown bot %s", bot_id)
        return
    if not store.add_utterance(bot_id, participant_id, speaker, text, start_ts):
        return  # Recall retried an event we already have
    if not detector.has_cue(text):
        return
    with _lock:
        window = store.recent_utterances(bot_id, WINDOW)
        existing = store.list_commitments(bot_id)
        for found in detector.detect(window, "live"):
            status = status_for(found["confidence"])
            if status is None or find_match(found, existing):
                continue
            store.add_commitment(bot_id, found, status)
            existing.append(found)
            log.info("%s commitment for %s: %s", status, found["owner"], found["action"])


def status_for(confidence):
    threshold = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.8"))
    if confidence >= threshold:
        return "proposed"
    if confidence >= 0.5:
        return "unsure"
    return None


def find_match(found, existing):
    """The stored commitment that says the same thing as `found`, or None."""
    for other in existing:
        if other["owner"].lower() != found["owner"].lower():
            continue
        if SequenceMatcher(None, other["action"].lower(), found["action"].lower()).ratio() > MATCH_RATIO:
            return other
    return None
