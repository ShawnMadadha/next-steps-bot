import json
import logging
import os
import re
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).with_name("prompt.md")

# The cheap gate. A new utterance with none of these words never reaches the model.
CUES = [
    "i'll", "i will", "we'll", "we will", "let me", "send", "share", "get you",
    "by friday", "by end of", "tomorrow", "next week", "follow up",
]


def has_cue(text):
    text = text.lower().replace("’", "'")
    return any(cue in text for cue in CUES)


def detect(utterances, mode, known=()):
    """utterances: rows or dicts with speaker and text. mode: "live" or "post_meeting".
    known: commitments the live pass already stored, so the post-meeting pass can reuse their wording."""
    if os.environ.get("DRY_RUN") == "1":
        return _stub(utterances, mode)
    content = f"Mode: {mode}\n\nTranscript:\n" + "\n".join(f"{u['speaker']}: {u['text']}" for u in utterances)
    if known:
        # The two passes are matched on action text, so the model is asked to keep the live wording for the same promise.
        content += "\n\nCommitments the live pass already found (owner | action | due):\n" + "\n".join(
            f"- {c['owner']}{_bracket(c)} | {c['action']} | {c['due'] or 'no due'}" for c in known)
    try:
        response = anthropic.Anthropic().messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=4096,
            system=PROMPT_PATH.read_text(),
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AnthropicError:
        log.exception("model call failed")
        return []
    text = "".join(block.text for block in response.content if block.type == "text")
    return parse(text)


def _bracket(c):
    owner_id = c["owner_id"] if "owner_id" in c.keys() else None
    return f" [{owner_id}]" if owner_id is not None else ""


def parse(text):
    """Model output to a list of commitments. Anything that is not the JSON we asked for becomes []."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        return [_commitment(item) for item in json.loads(text)]
    except (ValueError, TypeError, KeyError, AttributeError):
        log.warning("could not parse detector output: %r", text[:300])
        return []


def _commitment(item):
    return {
        "owner": str(item["owner"]).strip(),
        "owner_id": int(item["owner_id"]) if item.get("owner_id") not in (None, "") else None,
        "action": str(item["action"]).strip(),
        "due": item.get("due") or None,
        "confidence": float(item["confidence"]),
        "quote": item.get("quote") or "",
        "followup_draft": item.get("followup_draft") or None,
    }


# DRY_RUN=1: a regex stand-in so the whole pipeline runs with no model key. It is a stand-in, not a parser.
# Stops at punctuation or a conjunction, because low latency transcripts arrive with no punctuation at all.
STUB_CUE = re.compile(r"\b(I'll|I will|we'll|we will|let me)\s+(.+?)(?:[.!?]| and | but | so | or | then |$)", re.IGNORECASE)
STUB_DUE = re.compile(
    r"\b(by (?:end of )?(?:the |next )?(?:day|week|month|monday|tuesday|wednesday|thursday|friday|eod|eow)"
    r"|tomorrow|next week)\b",
    re.IGNORECASE,
)
STUB_SKIP = ("need", "revise", "check that")  # requirements and meta talk, not promises
STUB_SPEAKER = re.compile(r"^(.*?)(?: \[(\d+)\])?$")


def _stub(utterances, mode):
    found = []
    for u in utterances:
        text = u["text"].replace("\u2019", "'")
        for m in STUB_CUE.finditer(text):
            action = m.group(2).strip()
            if action.lower().startswith(STUB_SKIP):
                continue
            due = STUB_DUE.search(action)
            name, pid = STUB_SPEAKER.match(u["speaker"]).groups()
            c = {
                "owner": name,
                "owner_id": int(pid) if pid else None,
                "action": action,
                "due": due.group(0) if due else None,
                # A first person promise scores 0.9; "let me" is softer and lands in the unsure band.
                "confidence": 0.6 if m.group(1).lower() == "let me" else 0.9,
                "quote": u["text"],
                "followup_draft": None,
            }
            if mode == "post_meeting":
                c["followup_draft"] = f"Following up on our call: I'll {action}.\nShout if anything changes on your side."
                if re.search(r"\b(instead|revise)", text, re.IGNORECASE):
                    # A revision replaces the earlier promise from the same person that shares a distinctive word.
                    found = [e for e in found if e["owner"] != c["owner"] or not _keywords(e["action"]) & _keywords(action)]
            found.append(c)
    return found


def _keywords(action):
    return {w.lower() for w in re.findall(r"[A-Za-z]+", action) if len(w) >= 5 or (w.isupper() and len(w) > 1)}
